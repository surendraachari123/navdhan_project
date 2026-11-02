-- =====================================================================
-- Migration for an EXISTING database that already has app_users from
-- the old create_login_table.sql. Run this instead of the CREATE TABLE
-- version if app_users already exists with data in it you want to keep.
-- Safe to run once; re-running will error on the ALTERs that already
-- happened (Oracle has no "ADD COLUMN IF NOT EXISTS").
-- =====================================================================

ALTER TABLE app_users ADD email VARCHAR2(150);
ALTER TABLE app_users ADD role VARCHAR2(20) DEFAULT 'user' NOT NULL;
ALTER TABLE app_users ADD force_password_change NUMBER(1) DEFAULT 0 NOT NULL;

ALTER TABLE app_users ADD CONSTRAINT ck_app_users_force_pwd CHECK (force_password_change IN (0, 1));
ALTER TABLE app_users ADD CONSTRAINT ck_app_users_role CHECK (role IN ('admin', 'manager', 'user'));

-- Promote your existing login to admin so you can approve future
-- registration requests and manage users. Change 'your_username' first.
-- UPDATE app_users SET role = 'admin' WHERE username = 'your_username';

CREATE TABLE registration_requests (
    request_id      NUMBER
                        GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1)
                        PRIMARY KEY,
    username        VARCHAR2(50)  NOT NULL,
    full_name       VARCHAR2(150) NOT NULL,
    email           VARCHAR2(150) NOT NULL,
    mobile          VARCHAR2(20),
    requested_role  VARCHAR2(20)  DEFAULT 'user' NOT NULL,
    reason          VARCHAR2(500),
    status          VARCHAR2(20)  DEFAULT 'PENDING' NOT NULL,
    requested_at    DATE          DEFAULT SYSDATE NOT NULL,
    reviewed_at     DATE,
    reviewed_by     VARCHAR2(50),
    CONSTRAINT ck_reg_requests_status CHECK (status IN ('PENDING', 'APPROVED', 'REJECTED')),
    CONSTRAINT ck_reg_requests_role CHECK (requested_role IN ('admin', 'manager', 'user'))
);

CREATE UNIQUE INDEX idx_reg_requests_username ON registration_requests(username);

CREATE TABLE password_reset_tokens (
    token       VARCHAR2(64) PRIMARY KEY,
    username    VARCHAR2(50) NOT NULL,
    expires_at  DATE NOT NULL,
    used        NUMBER(1) DEFAULT 0 NOT NULL,
    created_at  DATE DEFAULT SYSDATE NOT NULL,
    CONSTRAINT ck_reset_tokens_used CHECK (used IN (0, 1)),
    CONSTRAINT fk_reset_tokens_user FOREIGN KEY (username)
        REFERENCES app_users(username)
);

COMMIT;
