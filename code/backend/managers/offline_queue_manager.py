"""Offline enrollment and APC queue management."""

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Dict, Any
from sqlalchemy.orm import Session

import models
from managers.vpn_config_manager import VPNConfigManager

LOGGER = logging.getLogger("certsvc.offline_queue")


class OfflineQueueManager:
    """Manages offline enrollment and APC requests stored in file-based queue."""

    def __init__(self, queue_dir: str = "/opt/certsvc/offline-queue"):
        """Initialize offline queue manager.
        
        Args:
            queue_dir: Path to offline queue directory
        """
        self.queue_dir = Path(queue_dir)
        self.queue_dir.mkdir(parents=True, exist_ok=True)
        self.vpn_cfg_mgr = VPNConfigManager()

    def save_enroll_request(
        self,
        hostname: str,
        region: str,
        assigned_ip: str,
        ca_cert: str,
        certificate: str,
        key: str,
        is_legacy: bool = False,
    ) -> str:
        """Save offline enroll request to queue.
        
        Args:
            hostname: Client hostname
            region: VPN region (sg, legacy, sfos)
            assigned_ip: Assigned IP address
            ca_cert: CA certificate content
            certificate: Client certificate content
            key: Client private key content
            is_legacy: Whether client uses legacy OpenVPN port
            
        Returns:
            Queue file path
        """
        request_data = {
            "type": "enroll",
            "hostname": hostname,
            "region": region,
            "assigned_ip": assigned_ip,
            "is_legacy": is_legacy,
            "ca_cert": ca_cert,
            "certificate": certificate,
            "key": key,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"enroll_{timestamp}_{hostname}.json"
        filepath = self.queue_dir / filename
        
        with open(filepath, "w") as f:
            json.dump(request_data, f, indent=2)
        
        LOGGER.info(f"Saved enroll request to queue: {filename}")
        return str(filepath)

    def save_apc_request(
        self,
        hostname: str,
        region: str,
        assigned_ip: str,
    ) -> str:
        """Save offline APC request to queue.
        
        Args:
            hostname: Client hostname
            region: VPN region (sg, legacy, sfos)
            assigned_ip: Assigned IP address
            
        Returns:
            Queue file path
        """
        request_data = {
            "type": "apc",
            "hostname": hostname,
            "region": region,
            "assigned_ip": assigned_ip,
            "timestamp": datetime.now(tz=timezone.utc).isoformat(),
        }
        
        timestamp = datetime.now(tz=timezone.utc).strftime("%Y%m%d_%H%M%S_%f")[:-3]
        filename = f"apc_{timestamp}_{hostname}.json"
        filepath = self.queue_dir / filename
        
        with open(filepath, "w") as f:
            json.dump(request_data, f, indent=2)
        
        LOGGER.info(f"Saved APC request to queue: {filename}")
        return str(filepath)

    def process_offline_queue(self, db: Session) -> Dict[str, Any]:
        """Process all pending offline requests from queue.
        
        Args:
            db: Database session
            
        Returns:
            Dict with processing results (processed, succeeded, failed)
        """
        results = {
            "processed": 0,
            "succeeded": 0,
            "failed": 0,
            "errors": [],
        }
        
        # Get all queue files, sorted by timestamp
        queue_files = sorted(self.queue_dir.glob("*.json"))
        
        if not queue_files:
            LOGGER.info("No offline queue files to process")
            return results
        
        LOGGER.info(f"Processing {len(queue_files)} offline queue files")
        
        for queue_file in queue_files:
            try:
                with open(queue_file, "r") as f:
                    request_data = json.load(f)
                
                request_type = request_data.get("type")
                
                if request_type == "enroll":
                    success = self._process_enroll_request(db, request_data)
                elif request_type == "apc":
                    success = self._process_apc_request(db, request_data)
                else:
                    raise ValueError(f"Unknown request type: {request_type}")
                
                results["processed"] += 1
                
                if success:
                    results["succeeded"] += 1
                    # Delete successfully processed file
                    queue_file.unlink()
                    LOGGER.info(f"Processed and deleted queue file: {queue_file.name}")
                else:
                    results["failed"] += 1
                    error_msg = f"Failed to process {queue_file.name}"
                    results["errors"].append(error_msg)
                    LOGGER.warning(error_msg)
                    
            except Exception as e:
                results["processed"] += 1
                results["failed"] += 1
                error_msg = f"Error processing {queue_file.name}: {str(e)}"
                results["errors"].append(error_msg)
                LOGGER.error(error_msg, exc_info=True)
        
        if results["succeeded"] > 0:
            LOGGER.info(
                f"Offline queue processing complete: "
                f"{results['succeeded']} succeeded, {results['failed']} failed"
            )
        
        return results

    def _process_enroll_request(self, db: Session, request_data: Dict[str, Any]) -> bool:
        """Process a single offline enroll request.
        
        Args:
            db: Database session
            request_data: Request data from queue file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            hostname = request_data.get("hostname")
            region = request_data.get("region")
            assigned_ip = request_data.get("assigned_ip")
            is_legacy = request_data.get("is_legacy", False)
            
            # Create or update client
            client = db.query(models.Client).filter_by(
                hostname=hostname, vpn_type="openvpn"
            ).first()
            
            if not client:
                client = models.Client(
                    hostname=hostname,
                    vpn_type="openvpn",
                    cert_cn=hostname,
                    is_legacy=is_legacy,
                    status="active",
                )
                db.add(client)
                db.flush()
            else:
                client.is_legacy = is_legacy
                client.status = "active"
                db.flush()
            
            # Create IP lease
            ip_lease = db.query(models.IPLease).filter_by(
                assigned_ip=assigned_ip
            ).first()
            
            if not ip_lease:
                ip_lease = models.IPLease(
                    client_id=client.id,
                    assigned_ip=assigned_ip,
                    is_active=True,
                )
                db.add(ip_lease)
                db.flush()
            else:
                ip_lease.client_id = client.id
                ip_lease.is_active = True
                db.flush()
            
            # Determine OpenVPN runtime settings based on region
            if region == "legacy":
                from app import OPENVPN_LEGACY_GATEWAY_IP, CCD_LEGACY, OPENVPN_PUSH_REMOTE_NETWORK_1
                ccd_dir = CCD_LEGACY
                route_gateway_ip = OPENVPN_LEGACY_GATEWAY_IP
            else:  # sg or default
                from app import OPENVPN_GATEWAY_IP, CCD, OPENVPN_PUSH_REMOTE_NETWORK_1
                ccd_dir = CCD
                route_gateway_ip = OPENVPN_GATEWAY_IP
            
            # Write CCD entry
            self.vpn_cfg_mgr.write_ccd_entry_and_route(
                ccd_dir=ccd_dir,
                hostname=hostname,
                assigned_ip=assigned_ip,
                route_gateway_ip=route_gateway_ip,
                push_remote_network=OPENVPN_PUSH_REMOTE_NETWORK_1,
            )
            
            db.commit()
            LOGGER.info(f"Processed enroll request for {hostname} at {assigned_ip}")
            return True
            
        except Exception as e:
            db.rollback()
            LOGGER.error(f"Failed to process enroll request: {str(e)}", exc_info=True)
            return False

    def _process_apc_request(self, db: Session, request_data: Dict[str, Any]) -> bool:
        """Process a single offline APC request.
        
        Args:
            db: Database session
            request_data: Request data from queue file
            
        Returns:
            True if successful, False otherwise
        """
        try:
            hostname = request_data.get("hostname")
            assigned_ip = request_data.get("assigned_ip")
            region = request_data.get("region")
            
            # Create or update SFOS client
            client = db.query(models.Client).filter_by(
                hostname=hostname, vpn_type="sfos"
            ).first()
            
            if not client:
                client = models.Client(
                    hostname=hostname,
                    vpn_type="sfos",
                    cert_cn=hostname,
                    status="active",
                )
                db.add(client)
                db.flush()
            else:
                client.status = "active"
                db.flush()
            
            # Create IP lease
            ip_lease = db.query(models.IPLease).filter_by(
                assigned_ip=assigned_ip
            ).first()
            
            if not ip_lease:
                ip_lease = models.IPLease(
                    client_id=client.id,
                    assigned_ip=assigned_ip,
                    is_active=True,
                )
                db.add(ip_lease)
                db.flush()
            else:
                ip_lease.client_id = client.id
                ip_lease.is_active = True
                db.flush()
            
            # Write CCD entry for SFOS
            from app import SFOS_GATEWAY_IP, CCD_SFOS, OPENVPN_PUSH_REMOTE_NETWORK_1
            
            self.vpn_cfg_mgr.write_ccd_entry_and_route(
                ccd_dir=CCD_SFOS,
                hostname=hostname,
                assigned_ip=assigned_ip,
                route_gateway_ip=SFOS_GATEWAY_IP,
                push_remote_network=OPENVPN_PUSH_REMOTE_NETWORK_1,
            )
            
            db.commit()
            LOGGER.info(f"Processed APC request for {hostname} at {assigned_ip}")
            return True
            
        except Exception as e:
            db.rollback()
            LOGGER.error(f"Failed to process APC request: {str(e)}", exc_info=True)
            return False
