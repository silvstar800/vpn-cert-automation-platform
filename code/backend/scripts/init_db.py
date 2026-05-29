from sqlalchemy import text

from db import Base, engine
import models

Base.metadata.create_all(bind=engine)

ddl_statements = [
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'clients'
                            AND table_schema = 'public'
              AND column_name = 'hostname'
        )
        AND EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'clients'
                            AND table_schema = 'public'
              AND column_name = 'vpn_type'
        )
        AND NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'uq_clients_hostname_vpn'
                            AND conrelid = 'public.clients'::regclass
        ) THEN
                        ALTER TABLE public.clients
            ADD CONSTRAINT uq_clients_hostname_vpn UNIQUE (hostname, vpn_type);
        END IF;
    END$$;
    """,
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'ip_leases'
                            AND table_schema = 'public'
              AND column_name = 'assigned_ip'
              AND udt_name <> 'inet'
        ) THEN
                        ALTER TABLE public.ip_leases
            ALTER COLUMN assigned_ip TYPE inet
            USING assigned_ip::inet;
        END IF;
    END$$;
    """,
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'ip_leases'
                            AND table_schema = 'public'
              AND column_name = 'vpn_type'
        )
        AND EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'ip_leases'
                            AND table_schema = 'public'
              AND column_name = 'identity'
        )
        AND NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'uq_ip_leases_vpn_identity'
                            AND conrelid = 'public.ip_leases'::regclass
        ) THEN
                        ALTER TABLE public.ip_leases
            ADD CONSTRAINT uq_ip_leases_vpn_identity UNIQUE (vpn_type, identity);
        END IF;
    END$$;
    """,
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'ip_leases'
                            AND table_schema = 'public'
              AND column_name = 'vpn_type'
        )
        AND EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'ip_leases'
                            AND table_schema = 'public'
              AND column_name = 'assigned_ip'
        )
        AND NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'uq_ip_leases_vpn_ip'
                            AND conrelid = 'public.ip_leases'::regclass
        ) THEN
                        ALTER TABLE public.ip_leases
            ADD CONSTRAINT uq_ip_leases_vpn_ip UNIQUE (vpn_type, assigned_ip);
        END IF;
    END$$;
    """,
    """
    DO $$
    BEGIN
        IF EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'clients'
                            AND table_schema = 'public'
              AND column_name = 'vpn_type'
        )
        AND EXISTS (
            SELECT 1
            FROM information_schema.columns
            WHERE table_name = 'clients'
                            AND table_schema = 'public'
              AND column_name = 'assigned_ip'
        )
        AND NOT EXISTS (
                        SELECT 1
                        FROM pg_constraint
                        WHERE conname = 'uq_clients_vpn_ip'
                            AND conrelid = 'public.clients'::regclass
        ) THEN
                        ALTER TABLE public.clients
            ADD CONSTRAINT uq_clients_vpn_ip UNIQUE (vpn_type, assigned_ip);
        END IF;
    END$$;
    """,
    """
    DO $$
    BEGIN
        IF NOT EXISTS (
            SELECT 1
            FROM pg_constraint
            WHERE conname = 'credentials_client_id_key'
              AND conrelid = 'public.credentials'::regclass
        ) THEN
            ALTER TABLE public.credentials
            ADD CONSTRAINT credentials_client_id_key UNIQUE (client_id);
        END IF;
    END$$;
    """,
]

with engine.begin() as conn:
    for ddl in ddl_statements:
        conn.execute(text(ddl))

print("[OK] DB schema initialized and constraints synced")
