"""Operational alerting and security-monitor state management."""

from __future__ import annotations

import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from zoneinfo import ZoneInfo


class AlertManager:
    """Manages security-monitor persistence and operational alert state."""

    def __init__(
        self,
        *,
        state_file: str,
        runtime_guard_log_file: str,
        monitor_window_seconds: int = 600,
        alert_threshold: int = 5,
        ban_threshold: int = 10,
        daily_summary_enabled: bool = False,
        daily_summary_hour: int = 8,
        daily_summary_min_overflow: int = 10,
        daily_summary_timezone: str = "Asia/Seoul",
        cert_expiry_alert_days: int = 30,
    ) -> None:
        self.state_file = Path(state_file)
        self.runtime_guard_log_file = Path(runtime_guard_log_file)
        self.monitor_window_seconds = int(monitor_window_seconds)
        self.alert_threshold = int(alert_threshold)
        self.ban_threshold = int(ban_threshold)
        self.daily_summary_enabled = bool(daily_summary_enabled)
        self.daily_summary_hour = int(daily_summary_hour)
        self.daily_summary_min_overflow = int(daily_summary_min_overflow)
        self.daily_summary_timezone = daily_summary_timezone or "Asia/Seoul"
        self.cert_expiry_alert_days = int(cert_expiry_alert_days)

        self.state_lock = threading.Lock()
        self.monitor_state_lock = threading.Lock()
        self.monitor_state: dict[str, Any] = {
            "services": {},
            "resource_alert_active": False,
            "expiry_sent_keys": set(),
        }

    def default_security_state(self) -> dict[str, Any]:
        """Build default persistent security monitoring state."""
        return {
            "settings": {
                "windowSeconds": self.monitor_window_seconds,
                "alertThreshold": self.alert_threshold,
                "banThreshold": self.ban_threshold,
            },
            "ip_stats": {},
            "events": [],
            "dailyOverflow": {},
            "dailySummary": {
                "enabled": self.daily_summary_enabled,
                "scheduleHour": self.daily_summary_hour,
                "minOverflowFailures": self.daily_summary_min_overflow,
                "timezone": self.daily_summary_timezone,
                "lastSentDate": "",
            },
        }

    def load_security_state(self) -> dict[str, Any]:
        """Load security monitoring state from disk, falling back to defaults."""
        if not self.state_file.exists():
            return self.default_security_state()
        try:
            with open(self.state_file, "r", encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, json.JSONDecodeError):
            return self.default_security_state()

        state = self.default_security_state()
        if isinstance(data, dict):
            state.update({k: v for k, v in data.items() if k in state})
            if isinstance(data.get("settings"), dict):
                state["settings"].update(data["settings"])
            if isinstance(data.get("dailySummary"), dict):
                state["dailySummary"].update(data["dailySummary"])
        return state

    def prune_daily_overflow(self, state: dict[str, Any], keep_days: int = 30) -> None:
        """Drop stale per-day overflow counters so the state file stays bounded."""
        cutoff = (datetime.now(tz=timezone.utc) - timedelta(days=keep_days)).strftime("%Y-%m-%d")
        daily_overflow = state.get("dailyOverflow", {})
        stale_keys = [key for key in list(daily_overflow) if key < cutoff]
        for key in stale_keys:
            del daily_overflow[key]

    def save_security_state(self, state: dict[str, Any]) -> None:
        """Persist security monitoring state to disk."""
        self.prune_daily_overflow(state)
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_file.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as handle:
            json.dump(state, handle, ensure_ascii=False, indent=2)
        tmp_path.replace(self.state_file)

    def append_security_event(self, state: dict[str, Any], event_type: str, **payload: Any) -> None:
        """Append a bounded in-state security event."""
        events = state.setdefault("events", [])
        event = {"ts": int(datetime.now(tz=timezone.utc).timestamp()), "type": event_type}
        event.update(payload)
        events.append(event)
        if len(events) > 300:
            del events[:-300]

    def build_daily_overflow_rows(self, daily_overflow: dict[str, Any]) -> list[dict[str, Any]]:
        """Flatten daily overflow counters into UI-friendly rows."""
        rows: list[dict[str, Any]] = []
        for date_key, date_entries in (daily_overflow or {}).items():
            if not isinstance(date_entries, dict):
                continue
            for ip, entry in date_entries.items():
                if not isinstance(entry, dict):
                    continue
                rows.append(
                    {
                        "date": date_key,
                        "ip": ip,
                        "overflowFailures": int(entry.get("overflowFailures", 0)),
                        "lastReason": entry.get("lastReason", ""),
                        "lastHostname": entry.get("lastHostname", ""),
                        "lastEndpoint": entry.get("lastEndpoint", ""),
                    }
                )
        rows.sort(key=lambda item: (item.get("date", ""), item.get("overflowFailures", 0)), reverse=True)
        return rows

    def build_daily_summary_candidates(self, daily_overflow: dict[str, Any], min_overflow_failures: int) -> list[dict[str, Any]]:
        """Return rows eligible for daily summary delivery."""
        rows = self.build_daily_overflow_rows(daily_overflow)
        return [row for row in rows if int(row.get("overflowFailures", 0)) >= int(min_overflow_failures)]

    def register_security_success(self, source_ip: str, endpoint: str, hostname: str = "") -> None:
        """Reset short-term failure counters after a successful monitored enroll."""
        if not source_ip:
            return
        with self.state_lock:
            state = self.load_security_state()
            ip_stats = state.setdefault("ip_stats", {})
            entry = ip_stats.setdefault(source_ip, {})
            entry["failures"] = []
            entry["lastSuccessTs"] = int(datetime.now(tz=timezone.utc).timestamp())
            entry["lastSuccessHostname"] = hostname
            entry["lastEndpoint"] = endpoint
            self.save_security_state(state)

    def register_security_failure(self, source_ip: str, endpoint: str, reason: str, hostname: str = "") -> dict[str, Any]:
        """Record an enroll failure, raise alerts, and mark bans when needed."""
        with self.state_lock:
            state = self.load_security_state()
            settings = state.setdefault("settings", {})
            window_seconds = int(settings.get("windowSeconds", self.monitor_window_seconds))
            alert_threshold = int(settings.get("alertThreshold", self.alert_threshold))
            ban_threshold = int(settings.get("banThreshold", self.ban_threshold))

            ip_stats = state.setdefault("ip_stats", {})
            entry = ip_stats.setdefault(source_ip, {})
            now_ts = int(datetime.now(tz=timezone.utc).timestamp())
            failures = [ts for ts in entry.get("failures", []) if now_ts - int(ts) <= window_seconds]
            previous_failure_count = len(failures)
            failures.append(now_ts)
            entry["failures"] = failures
            entry["lastFailureTs"] = now_ts
            entry["lastFailureReason"] = reason
            entry["lastHostname"] = hostname
            entry["lastEndpoint"] = endpoint
            entry["cumulativeFailures"] = int(entry.get("cumulativeFailures", 0)) + 1

            failure_count = len(failures)
            self.append_security_event(
                state,
                "failure",
                ip=source_ip,
                endpoint=endpoint,
                reason=reason,
                hostname=hostname,
                failureCount=failure_count,
            )

            daily_key = datetime.now(tz=timezone.utc).strftime("%Y-%m-%d")
            daily_overflow = state.setdefault("dailyOverflow", {})
            daily_entry = daily_overflow.setdefault(daily_key, {}).setdefault(
                source_ip,
                {"overflowFailures": 0, "lastReason": "", "lastHostname": "", "lastEndpoint": endpoint},
            )

            if failure_count > alert_threshold:
                daily_entry["overflowFailures"] = int(daily_entry.get("overflowFailures", 0)) + 1
                daily_entry["lastReason"] = reason
                daily_entry["lastHostname"] = hostname
                daily_entry["lastEndpoint"] = endpoint

            should_alert = previous_failure_count < alert_threshold <= failure_count
            if should_alert:
                entry["lastAlertTs"] = now_ts
                entry["lastAlertFailureCount"] = failure_count
                self.append_security_event(
                    state,
                    "alert",
                    ip=source_ip,
                    endpoint=endpoint,
                    reason=reason,
                    hostname=hostname,
                    failureCount=failure_count,
                )

            if failure_count >= ban_threshold and entry.get("status") != "banned":
                entry["status"] = "banned"
                entry["bannedAt"] = now_ts
                self.append_security_event(
                    state,
                    "ban",
                    ip=source_ip,
                    endpoint=endpoint,
                    reason=reason,
                    hostname=hostname,
                    failureCount=failure_count,
                )

            self.save_security_state(state)
            return {
                "failureCount": failure_count,
                "banned": entry.get("status") == "banned",
                "shouldAlert": should_alert,
            }

    def is_source_ip_banned(self, source_ip: str) -> bool:
        """Return whether an IP is currently marked as banned."""
        if not source_ip:
            return False
        with self.state_lock:
            state = self.load_security_state()
            entry = state.get("ip_stats", {}).get(source_ip, {})
            return entry.get("status") == "banned"

    def update_monitor_settings(self, *, alert_threshold: int, ban_threshold: int, window_seconds: int) -> None:
        """Update persisted monitor thresholds."""
        with self.state_lock:
            state = self.load_security_state()
            state.setdefault("settings", {})
            state["settings"].update(
                {
                    "alertThreshold": int(alert_threshold),
                    "banThreshold": int(ban_threshold),
                    "windowSeconds": int(window_seconds),
                }
            )
            self.append_security_event(
                state,
                "settings_update",
                alertThreshold=int(alert_threshold),
                banThreshold=int(ban_threshold),
                windowSeconds=int(window_seconds),
            )
            self.save_security_state(state)

    def unban_ip(self, target_ip: str, note: str = "") -> None:
        """Release a banned IP after manual review."""
        with self.state_lock:
            state = self.load_security_state()
            ip_stats = state.setdefault("ip_stats", {})
            entry = ip_stats.setdefault(target_ip, {})
            entry["status"] = "released"
            entry["failures"] = []
            entry["releasedAt"] = int(datetime.now(tz=timezone.utc).timestamp())
            entry["releaseNote"] = (note or "").strip()
            self.append_security_event(state, "unban", ip=target_ip, note=entry["releaseNote"])
            self.save_security_state(state)

    def get_security_monitor_payload(
        self,
        *,
        record: Any,
        webhook_enabled: bool,
        slack_enabled: bool,
        slack_channel: str,
    ) -> dict[str, Any]:
        """Build the security-monitor API payload from persisted state."""
        with self.state_lock:
            state = self.load_security_state()
        ip_stats = state.get("ip_stats", {})
        daily_overflow = state.get("dailyOverflow", {})
        daily_summary = state.get("dailySummary", self.default_security_state()["dailySummary"])
        daily_summary = {**daily_summary, "enabled": slack_enabled}

        rows = []
        for ip, entry in ip_stats.items():
            rows.append(
                {
                    "ip": ip,
                    "status": entry.get("status", "clear"),
                    "failureCount": len(entry.get("failures", [])),
                    "cumulativeFailures": int(entry.get("cumulativeFailures", 0)),
                    "lastFailureTs": entry.get("lastFailureTs"),
                    "lastFailureReason": entry.get("lastFailureReason", ""),
                    "lastHostname": entry.get("lastHostname", ""),
                    "lastEndpoint": entry.get("lastEndpoint", ""),
                    "bannedAt": entry.get("bannedAt"),
                    "releasedAt": entry.get("releasedAt"),
                    "releaseNote": entry.get("releaseNote", ""),
                    "lastAlertTs": entry.get("lastAlertTs"),
                }
            )
        rows.sort(key=lambda item: item.get("lastFailureTs") or 0, reverse=True)
        return {
            "settings": state.get("settings", self.default_security_state()["settings"]),
            "rows": rows,
            "recentEvents": list(reversed(state.get("events", [])[-50:])),
            "webhookEnabled": bool(webhook_enabled),
            "slackEnabled": bool(slack_enabled),
            "slackChannel": slack_channel,
            "dailyOverflow": daily_overflow,
            "dailyOverflowRows": self.build_daily_overflow_rows(daily_overflow),
            "dailySummary": daily_summary,
            "dailySummaryCandidates": self.build_daily_summary_candidates(
                daily_overflow,
                int(daily_summary.get("minOverflowFailures", self.daily_summary_min_overflow)),
            ),
        }

    def tail_log_lines(self, max_lines: int = 200) -> list[str]:
        """Return the tail of the runtime-guard log file."""
        if not self.runtime_guard_log_file.exists():
            return []
        try:
            lines = self.runtime_guard_log_file.read_text(encoding="utf-8", errors="ignore").splitlines()
            return lines[-max_lines:]
        except OSError:
            return []

    def parse_runtime_guard_entries(self, max_lines: int = 200) -> list[dict[str, Any]]:
        """Return runtime-guard log lines as structured rows."""
        entries = []
        for index, line in enumerate(reversed(self.tail_log_lines(max_lines=max_lines))):
            stripped = line.strip()
            if not stripped:
                continue
            match = re.match(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (.*)$", stripped)
            ts_label = ""
            message = stripped
            level = "INFO"
            if match:
                raw_ts, message = match.groups()
                try:
                    ts_label = (
                        datetime.strptime(raw_ts, "%Y-%m-%d %H:%M:%S")
                        .replace(tzinfo=timezone.utc)
                        .astimezone(ZoneInfo(self.daily_summary_timezone))
                        .strftime("%Y-%m-%d %H:%M:%S")
                    )
                except Exception:
                    ts_label = raw_ts
            level_match = re.search(r"\b(INFO|WARN|ERROR|OK)\b", message)
            if level_match:
                level = level_match.group(1)
            entries.append({"id": f"guard-{index}", "ts": ts_label, "message": message, "level": level})
        return entries

    def process_operational_alerts(
        self,
        *,
        record: Any,
        services: list[dict[str, Any]],
        resources: dict[str, Any],
        active_clients: Iterable[Any],
        assigned_ip_map: dict[int, str],
        get_certificate_expire_at: Callable[[Any], str],
        send_slack: Callable[[str, dict[str, Any]], None],
    ) -> None:
        """Process service, resource, certificate, and daily-summary alerts."""
        today = datetime.now().date()
        state = self.load_security_state()
        daily_summary = state.get("dailySummary", self.default_security_state()["dailySummary"])

        with self.monitor_state_lock:
            previous_services = self.monitor_state.setdefault("services", {})
            expiry_sent_keys = self.monitor_state.setdefault("expiry_sent_keys", set())
            resource_alert_active = bool(self.monitor_state.get("resource_alert_active", False))

            if record.slack_enabled and record.slack_bot_token_enc and bool(record.slack_notify_service_down):
                for service in services:
                    name = service["name"]
                    current_status = service["status"]
                    previous_status = previous_services.get(name)
                    if current_status != service["nominal"] and previous_status != current_status:
                        send_slack(
                            "service_down",
                            {
                                "serviceName": name,
                                "currentStatus": current_status,
                                "port": service.get("port", "-"),
                                "detectedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            },
                        )
                    if previous_status is not None and previous_status != service["nominal"] and current_status == service["nominal"]:
                        send_slack(
                            "service_recovered",
                            {
                                "serviceName": name,
                                "previousStatus": previous_status,
                                "currentStatus": current_status,
                                "port": service.get("port", "-"),
                                "detectedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            },
                        )
                    previous_services[name] = current_status

            cpu_info = resources.get("cpu", {})
            memory_info = resources.get("memory", {})
            disk_info = resources.get("disk", {}) or resources.get("disk_root", {})
            cpu_percent = float(cpu_info.get("usage_percent", cpu_info.get("usage_percent_est", 0.0)))
            memory_percent = float(memory_info.get("used_percent", 0.0))
            disk_percent = float(disk_info.get("used_percent", 0.0))
            resource_threshold_hit = (
                cpu_percent >= int(record.slack_cpu_threshold or 90)
                or memory_percent >= int(record.slack_memory_threshold or 90)
                or disk_percent >= int(record.slack_disk_threshold or 90)
            )
            if (
                record.slack_enabled
                and record.slack_bot_token_enc
                and bool(record.slack_notify_resource_threshold)
                and resource_threshold_hit
                and not resource_alert_active
            ):
                send_slack(
                    "resource_threshold",
                    {
                        "cpuPercent": cpu_percent,
                        "memoryPercent": memory_percent,
                        "diskPercent": disk_percent,
                    },
                )
            self.monitor_state["resource_alert_active"] = resource_threshold_hit

            if record.slack_enabled and record.slack_bot_token_enc and bool(record.slack_notify_certificate_expiry):
                keep_keys = set()
                for client in active_clients:
                    expire_at = get_certificate_expire_at(client)
                    if not expire_at:
                        continue
                    expire_date = datetime.fromisoformat(expire_at).date()
                    days_left = (expire_date - today).days
                    if days_left < 0 or days_left > self.cert_expiry_alert_days:
                        continue
                    key = f"{today.isoformat()}:{getattr(client, 'hostname', '')}:{expire_at}"
                    keep_keys.add(key)
                    if key in expiry_sent_keys:
                        continue
                    send_slack(
                        "certificate_expiry",
                        {
                            "hostname": getattr(client, "hostname", ""),
                            "assignedIp": assigned_ip_map.get(getattr(client, "id", 0), "-"),
                            "expireAt": expire_at,
                            "daysLeft": days_left,
                        },
                    )
                    expiry_sent_keys.add(key)
                self.monitor_state["expiry_sent_keys"] = {
                    key for key in expiry_sent_keys if key in keep_keys or key.startswith(today.isoformat())
                }

        if record.slack_enabled and record.slack_bot_token_enc:
            tz_name = str(daily_summary.get("timezone", self.daily_summary_timezone) or self.daily_summary_timezone)
            try:
                summary_tz = ZoneInfo(tz_name)
            except Exception:
                summary_tz = ZoneInfo(self.daily_summary_timezone)
                tz_name = self.daily_summary_timezone
            now_local = datetime.now(summary_tz)
            target_date = (now_local - timedelta(days=1)).date().isoformat()
            last_sent_date = str(daily_summary.get("lastSentDate", "") or "")
            schedule_hour = int(daily_summary.get("scheduleHour", self.daily_summary_hour))
            min_overflow = int(daily_summary.get("minOverflowFailures", self.daily_summary_min_overflow))
            if now_local.hour >= schedule_hour and last_sent_date != target_date:
                candidates = [
                    row
                    for row in self.build_daily_summary_candidates(state.get("dailyOverflow", {}), min_overflow)
                    if str(row.get("date", "")) == target_date
                ]
                summary_line = ", ".join(f"{row.get('ip')}({row.get('overflowFailures')}회)" for row in candidates[:5]) if candidates else "후보가 없습니다."
                send_slack(
                    "daily_summary",
                    {
                        "targetDate": target_date,
                        "candidateCount": len(candidates),
                        "minOverflowFailures": min_overflow,
                        "channel": record.slack_channel or "",
                        "summaryLine": summary_line,
                    },
                )
                with self.state_lock:
                    latest_state = self.load_security_state()
                    latest_summary = latest_state.get("dailySummary", self.default_security_state()["dailySummary"])
                    latest_summary["lastSentDate"] = target_date
                    latest_summary["enabled"] = True
                    latest_state["dailySummary"] = latest_summary
                    self.save_security_state(latest_state)
