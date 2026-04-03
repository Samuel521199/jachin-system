-- =============================================================================
-- Jachin L1 (Nexus) 全量表结构（与 cloud/nexus drizzle-kit push + init-store 后一致）
-- 服务器仅需：PostgreSQL + psql（或 docker run postgres psql），无需 Node / 无需仓库代码
-- 使用顺序：
--   1) psql ... -f l1_reset_public_schema.sql
--   2) psql ... -v ON_ERROR_STOP=1 -f l1_nexus_full_schema.sql
-- 再生成本文件（开发机在仓库根目录，需 Docker）：
--   见 README.txt「再生成全量 SQL」
-- =============================================================================

--
-- PostgreSQL database dump
--


-- Dumped from database version 16.13 (Debian 16.13-1.pgdg12+1)
-- Dumped by pg_dump version 16.13 (Debian 16.13-1.pgdg12+1)

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: device_group_member_role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.device_group_member_role AS ENUM (
    'admin',
    'viewer'
);


--
-- Name: edge_agent_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.edge_agent_status AS ENUM (
    'pending',
    'active',
    'offline'
);


--
-- Name: item_type; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.item_type AS ENUM (
    'SKILL',
    'MCP'
);


--
-- Name: license_status; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.license_status AS ENUM (
    'ACTIVE',
    'EXPIRED',
    'REVOKED'
);


--
-- Name: org_role; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.org_role AS ENUM (
    'owner',
    'admin',
    'member',
    'fleet_admin',
    'viewer'
);


--
-- Name: runtime_tier; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.runtime_tier AS ENUM (
    'L3_LOCAL',
    'L2_GATEWAY',
    'L1_CLOUD'
);


--
-- Name: visibility; Type: TYPE; Schema: public; Owner: -
--

CREATE TYPE public.visibility AS ENUM (
    'PUBLIC',
    'PRIVATE'
);


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.accounts (
    user_id text NOT NULL,
    type text NOT NULL,
    provider text NOT NULL,
    provider_account_id text NOT NULL,
    refresh_token text,
    access_token text,
    expires_at integer,
    token_type text,
    scope text,
    id_token text,
    session_state text
);


--
-- Name: agent_message_queue; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.agent_message_queue (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    agent_id uuid NOT NULL,
    message_text text NOT NULL,
    direction text DEFAULT 'inbound'::text NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    source_meta jsonb,
    processed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: blueprints; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.blueprints (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    creator_id text,
    organization_id uuid,
    name text NOT NULL,
    description text,
    ast_json jsonb DEFAULT '{}'::jsonb NOT NULL,
    price numeric(12,4) DEFAULT '0'::numeric,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: deploy_commands; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.deploy_commands (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id text NOT NULL,
    layer2_instance_id text NOT NULL,
    resource_type text DEFAULT 'plugin'::text NOT NULL,
    resource_id uuid NOT NULL,
    plugin_id text,
    download_url text NOT NULL,
    temp_token text NOT NULL,
    token_expires_at timestamp with time zone NOT NULL,
    status text DEFAULT 'pending'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: developer_payouts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.developer_payouts (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    developer_id text NOT NULL,
    item_id text NOT NULL,
    total_calls integer DEFAULT 0 NOT NULL,
    unpaid_amount_cents integer DEFAULT 0 NOT NULL,
    paid_amount_cents integer DEFAULT 0 NOT NULL,
    last_updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: device_group_members; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.device_group_members (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    group_id uuid NOT NULL,
    user_id text NOT NULL,
    role public.device_group_member_role DEFAULT 'viewer'::public.device_group_member_role NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: device_groups; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.device_groups (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    name text NOT NULL,
    description text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: edge_agents; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.edge_agents (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    user_id text,
    organization_id uuid,
    device_group_id uuid,
    name text,
    pairing_code character varying(6) NOT NULL,
    status public.edge_agent_status DEFAULT 'pending'::public.edge_agent_status NOT NULL,
    current_blueprint_id uuid,
    auth_token text,
    pairing_expires_at timestamp with time zone,
    last_heartbeat timestamp with time zone,
    im_binding_id text,
    im_platform text DEFAULT 'telegram'::text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: organization_users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organization_users (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    org_id uuid NOT NULL,
    user_id text NOT NULL,
    role public.org_role DEFAULT 'member'::public.org_role NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: organizations; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.organizations (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    name text NOT NULL,
    slug character varying(64),
    billing_plan text DEFAULT 'free'::text,
    is_personal_default boolean DEFAULT false NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: platform_admins; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.platform_admins (
    id text NOT NULL,
    username text NOT NULL,
    password_hash text NOT NULL,
    role text DEFAULT 'super_admin'::text NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: plugins_registry; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.plugins_registry (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    plugin_id text NOT NULL,
    version text DEFAULT '1.0.0'::text NOT NULL,
    item_type public.item_type NOT NULL,
    name text NOT NULL,
    description text,
    developer_id text,
    visibility public.visibility DEFAULT 'PRIVATE'::public.visibility NOT NULL,
    price_monthly integer DEFAULT 0 NOT NULL,
    runtime_tier public.runtime_tier NOT NULL,
    required_mcps jsonb DEFAULT '[]'::jsonb,
    package_url text,
    package_sha256 text,
    category text DEFAULT 'skill'::text,
    download_count integer DEFAULT 0,
    manifest_json jsonb,
    status text DEFAULT 'pending'::text NOT NULL,
    reject_reason text,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: sessions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.sessions (
    session_token text NOT NULL,
    user_id text NOT NULL,
    expires timestamp without time zone NOT NULL
);


--
-- Name: telemetry_logs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.telemetry_logs (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id text NOT NULL,
    original_id text NOT NULL,
    sub_account_id text,
    item_id text NOT NULL,
    action_name text NOT NULL,
    status text NOT NULL,
    latency_ms numeric(12,2),
    "timestamp" numeric(15,4) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: transactions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.transactions (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    buyer_id text NOT NULL,
    organization_id uuid,
    resource_type text NOT NULL,
    resource_id uuid NOT NULL,
    resource_plugin_id text,
    action text DEFAULT 'acquire'::text NOT NULL,
    license_key text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: user_licenses; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.user_licenses (
    id uuid DEFAULT gen_random_uuid() NOT NULL,
    tenant_id text NOT NULL,
    item_id uuid NOT NULL,
    status public.license_status DEFAULT 'ACTIVE'::public.license_status NOT NULL,
    purchased_at timestamp with time zone DEFAULT now() NOT NULL,
    expires_at timestamp with time zone
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id text NOT NULL,
    name text,
    email text,
    email_verified timestamp without time zone,
    image text,
    password_hash text,
    is_root boolean DEFAULT false
);


--
-- Name: verification_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.verification_tokens (
    identifier text NOT NULL,
    token text NOT NULL,
    expires timestamp without time zone NOT NULL
);


--
-- Name: accounts accounts_provider_provider_account_id_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_provider_provider_account_id_pk PRIMARY KEY (provider, provider_account_id);


--
-- Name: agent_message_queue agent_message_queue_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_message_queue
    ADD CONSTRAINT agent_message_queue_pkey PRIMARY KEY (id);


--
-- Name: blueprints blueprints_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blueprints
    ADD CONSTRAINT blueprints_pkey PRIMARY KEY (id);


--
-- Name: deploy_commands deploy_commands_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.deploy_commands
    ADD CONSTRAINT deploy_commands_pkey PRIMARY KEY (id);


--
-- Name: developer_payouts developer_payouts_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.developer_payouts
    ADD CONSTRAINT developer_payouts_pkey PRIMARY KEY (id);


--
-- Name: device_group_members device_group_members_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_group_members
    ADD CONSTRAINT device_group_members_pkey PRIMARY KEY (id);


--
-- Name: device_groups device_groups_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_groups
    ADD CONSTRAINT device_groups_pkey PRIMARY KEY (id);


--
-- Name: edge_agents edge_agents_pairing_code_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edge_agents
    ADD CONSTRAINT edge_agents_pairing_code_unique UNIQUE (pairing_code);


--
-- Name: edge_agents edge_agents_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edge_agents
    ADD CONSTRAINT edge_agents_pkey PRIMARY KEY (id);


--
-- Name: organization_users organization_users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_users
    ADD CONSTRAINT organization_users_pkey PRIMARY KEY (id);


--
-- Name: organizations organizations_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organizations
    ADD CONSTRAINT organizations_pkey PRIMARY KEY (id);


--
-- Name: organizations_slug_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX organizations_slug_unique ON public.organizations USING btree (slug);


--
-- Name: platform_admins platform_admins_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_admins
    ADD CONSTRAINT platform_admins_pkey PRIMARY KEY (id);


--
-- Name: platform_admins platform_admins_username_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.platform_admins
    ADD CONSTRAINT platform_admins_username_unique UNIQUE (username);


--
-- Name: plugins_registry plugins_registry_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plugins_registry
    ADD CONSTRAINT plugins_registry_pkey PRIMARY KEY (id);


--
-- Name: plugins_registry plugins_registry_plugin_id_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.plugins_registry
    ADD CONSTRAINT plugins_registry_plugin_id_unique UNIQUE (plugin_id);


--
-- Name: sessions sessions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_pkey PRIMARY KEY (session_token);


--
-- Name: telemetry_logs telemetry_logs_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.telemetry_logs
    ADD CONSTRAINT telemetry_logs_pkey PRIMARY KEY (id);


--
-- Name: transactions transactions_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_pkey PRIMARY KEY (id);


--
-- Name: user_licenses user_licenses_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_licenses
    ADD CONSTRAINT user_licenses_pkey PRIMARY KEY (id);


--
-- Name: users users_email_unique; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_email_unique UNIQUE (email);


--
-- Name: users users_pkey; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT users_pkey PRIMARY KEY (id);


--
-- Name: verification_tokens verification_tokens_identifier_token_pk; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.verification_tokens
    ADD CONSTRAINT verification_tokens_identifier_token_pk PRIMARY KEY (identifier, token);


--
-- Name: developer_payouts_dev_item; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX developer_payouts_dev_item ON public.developer_payouts USING btree (developer_id, item_id);


--
-- Name: device_group_members_group_user_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX device_group_members_group_user_unique ON public.device_group_members USING btree (group_id, user_id);


--
-- Name: device_group_members_user_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX device_group_members_user_id_idx ON public.device_group_members USING btree (user_id);


--
-- Name: device_groups_org_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX device_groups_org_id_idx ON public.device_groups USING btree (org_id);


--
-- Name: device_groups_org_id_name_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX device_groups_org_id_name_unique ON public.device_groups USING btree (org_id, name);


--
-- Name: edge_agents_device_group_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX edge_agents_device_group_id_idx ON public.edge_agents USING btree (device_group_id);


--
-- Name: edge_agents_organization_id_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX edge_agents_organization_id_idx ON public.edge_agents USING btree (organization_id);


--
-- Name: idx_telemetry_logs_tenant_ts; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_telemetry_logs_tenant_ts ON public.telemetry_logs USING btree (tenant_id, "timestamp");


--
-- Name: plugins_registry_status_created_idx; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX plugins_registry_status_created_idx ON public.plugins_registry USING btree (status, created_at);


--
-- Name: user_licenses_tenant_item_unique; Type: INDEX; Schema: public; Owner: -
--

CREATE UNIQUE INDEX user_licenses_tenant_item_unique ON public.user_licenses USING btree (tenant_id, item_id);


--
-- Name: accounts accounts_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.accounts
    ADD CONSTRAINT accounts_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: agent_message_queue agent_message_queue_agent_id_edge_agents_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.agent_message_queue
    ADD CONSTRAINT agent_message_queue_agent_id_edge_agents_id_fk FOREIGN KEY (agent_id) REFERENCES public.edge_agents(id) ON DELETE CASCADE;


--
-- Name: blueprints blueprints_creator_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blueprints
    ADD CONSTRAINT blueprints_creator_id_users_id_fk FOREIGN KEY (creator_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: blueprints blueprints_organization_id_organizations_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.blueprints
    ADD CONSTRAINT blueprints_organization_id_organizations_id_fk FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE SET NULL;


--
-- Name: device_group_members device_group_members_group_id_device_groups_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_group_members
    ADD CONSTRAINT device_group_members_group_id_device_groups_id_fk FOREIGN KEY (group_id) REFERENCES public.device_groups(id) ON DELETE CASCADE;


--
-- Name: device_group_members device_group_members_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_group_members
    ADD CONSTRAINT device_group_members_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: device_groups device_groups_org_id_organizations_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.device_groups
    ADD CONSTRAINT device_groups_org_id_organizations_id_fk FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE CASCADE;


--
-- Name: edge_agents edge_agents_current_blueprint_id_blueprints_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edge_agents
    ADD CONSTRAINT edge_agents_current_blueprint_id_blueprints_id_fk FOREIGN KEY (current_blueprint_id) REFERENCES public.blueprints(id) ON DELETE SET NULL;


--
-- Name: edge_agents edge_agents_device_group_id_device_groups_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edge_agents
    ADD CONSTRAINT edge_agents_device_group_id_device_groups_id_fk FOREIGN KEY (device_group_id) REFERENCES public.device_groups(id) ON DELETE SET NULL;


--
-- Name: edge_agents edge_agents_organization_id_organizations_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edge_agents
    ADD CONSTRAINT edge_agents_organization_id_organizations_id_fk FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE SET NULL;


--
-- Name: edge_agents edge_agents_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.edge_agents
    ADD CONSTRAINT edge_agents_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: organization_users organization_users_org_id_organizations_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_users
    ADD CONSTRAINT organization_users_org_id_organizations_id_fk FOREIGN KEY (org_id) REFERENCES public.organizations(id) ON DELETE CASCADE;


--
-- Name: organization_users organization_users_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.organization_users
    ADD CONSTRAINT organization_users_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: sessions sessions_user_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.sessions
    ADD CONSTRAINT sessions_user_id_users_id_fk FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: transactions transactions_buyer_id_users_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_buyer_id_users_id_fk FOREIGN KEY (buyer_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: transactions transactions_organization_id_organizations_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.transactions
    ADD CONSTRAINT transactions_organization_id_organizations_id_fk FOREIGN KEY (organization_id) REFERENCES public.organizations(id) ON DELETE SET NULL;


--
-- Name: user_licenses user_licenses_item_id_plugins_registry_id_fk; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.user_licenses
    ADD CONSTRAINT user_licenses_item_id_plugins_registry_id_fk FOREIGN KEY (item_id) REFERENCES public.plugins_registry(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--


