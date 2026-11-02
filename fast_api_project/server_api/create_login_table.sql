-- =====================================================================
-- APP_USERS + role-based access + self-service registration/password
-- reset tables for the NavDhan API.
-- Run this once, connected as the surendra_nov25 schema user.
-- =====================================================================

-- ---------------------------------------------------------------------
-- 1. app_users (login table) - now with role + forced-password-change
-- ---------------------------------------------------------------------
CREATE TABLE app_users (
    user_id               NUMBER
                              GENERATED ALWAYS AS IDENTITY (START WITH 1 INCREMENT BY 1)
                              PRIMARY KEY,
    username              VARCHAR2(50)  NOT NULL,
    password_hash         VARCHAR2(200) NOT NULL,   -- bcrypt hash, never plaintext
    email                 VARCHAR2(150),
    role                  VARCHAR2(20)  DEFAULT 'user' NOT NULL,
    is_active             NUMBER(1)     DEFAULT 1 NOT NULL,
    -- Set to 1 whenever a temp password is issued (new account approval,
    -- or a password reset). The client is expected to force a
    -- "change your password" screen while this is 1.
    force_password_change NUMBER(1)     DEFAULT 0 NOT NULL,
    created_at            DATE          DEFAULT SYSDATE NOT NULL,
    last_login            DATE,
    CONSTRAINT uq_app_users_username UNIQUE (username),
    CONSTRAINT ck_app_users_is_active CHECK (is_active IN (0, 1)),
    CONSTRAINT ck_app_users_force_pwd CHECK (force_password_change IN (0, 1)),
    CONSTRAINT ck_app_users_role CHECK (role IN ('admin', 'manager', 'user'))
);

-- ---------------------------------------------------------------------
-- 2. registration_requests - the public "sign up" form lands here.
-- Nothing in app_users is created until an admin approves the row below.
-- ---------------------------------------------------------------------
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

-- One pending/approved request per username at a time.
CREATE UNIQUE INDEX idx_reg_requests_username ON registration_requests(username);

-- ---------------------------------------------------------------------
-- 3. password_reset_tokens - one-time-use, time-limited tokens issued
-- by POST /forgot-password and consumed by POST /reset-password.
-- ---------------------------------------------------------------------
CREATE TABLE password_reset_tokens (
    token       VARCHAR2(64) PRIMARY KEY,   -- random urlsafe token, not guessable
    username    VARCHAR2(50) NOT NULL,
    expires_at  DATE NOT NULL,
    used        NUMBER(1) DEFAULT 0 NOT NULL,
    created_at  DATE DEFAULT SYSDATE NOT NULL,
    CONSTRAINT ck_reset_tokens_used CHECK (used IN (0, 1)),
    CONSTRAINT fk_reset_tokens_user FOREIGN KEY (username)
        REFERENCES app_users(username)
);

COMMIT;
