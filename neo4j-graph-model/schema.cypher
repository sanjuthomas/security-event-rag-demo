// Constraints — one node per business key
CREATE CONSTRAINT instruction_id_unique IF NOT EXISTS
FOR (i:Instruction) REQUIRE i.instruction_id IS UNIQUE;

CREATE CONSTRAINT instruction_version_key_unique IF NOT EXISTS
FOR (v:InstructionVersion) REQUIRE v.version_key IS UNIQUE;

CREATE CONSTRAINT instruction_version_id_num_unique IF NOT EXISTS
FOR (v:InstructionVersion) REQUIRE (v.instruction_id, v.version_number) IS UNIQUE;

CREATE CONSTRAINT security_event_id_unique IF NOT EXISTS
FOR (e:SecurityEvent) REQUIRE e.event_id IS UNIQUE;

CREATE CONSTRAINT user_id_unique IF NOT EXISTS
FOR (u:User) REQUIRE u.user_id IS UNIQUE;

CREATE CONSTRAINT profit_center_lob_unique IF NOT EXISTS
FOR (p:ProfitCenter) REQUIRE p.lob IS UNIQUE;

// Indexes — common filter and traversal paths
CREATE INDEX instruction_version_status IF NOT EXISTS
FOR (v:InstructionVersion) ON (v.status);

CREATE INDEX instruction_version_owning_lob IF NOT EXISTS
FOR (v:InstructionVersion) ON (v.owning_lob);

CREATE INDEX security_event_timestamp IF NOT EXISTS
FOR (e:SecurityEvent) ON (e.timestamp);

CREATE INDEX security_event_severity IF NOT EXISTS
FOR (e:SecurityEvent) ON (e.severity);

CREATE INDEX security_event_action IF NOT EXISTS
FOR (e:SecurityEvent) ON (e.action);

CREATE INDEX user_lob IF NOT EXISTS
FOR (u:User) ON (u.lob);

CREATE INDEX instruction_version_creditor_account IF NOT EXISTS
FOR (v:InstructionVersion) ON (v.creditor_account_id);

CREATE INDEX instruction_version_debtor_account IF NOT EXISTS
FOR (v:InstructionVersion) ON (v.debtor_account_id);

CREATE INDEX instruction_version_currency IF NOT EXISTS
FOR (v:InstructionVersion) ON (v.currency);

CREATE INDEX instruction_version_is_expired IF NOT EXISTS
FOR (v:InstructionVersion) ON (v.is_expired);

CREATE INDEX instruction_version_effective_date IF NOT EXISTS
FOR (v:InstructionVersion) ON (v.effective_date);

CREATE INDEX instruction_version_end_date IF NOT EXISTS
FOR (v:InstructionVersion) ON (v.end_date);

// Payment constraints and indexes
CREATE CONSTRAINT payment_id_version_unique IF NOT EXISTS
FOR (p:Payment) REQUIRE (p.payment_id, p.version_number) IS UNIQUE;

CREATE INDEX payment_version_key IF NOT EXISTS
FOR (p:Payment) ON (p.version_key);

CREATE INDEX payment_instruction_id IF NOT EXISTS
FOR (p:Payment) ON (p.instruction_id);

CREATE INDEX payment_status IF NOT EXISTS
FOR (p:Payment) ON (p.status);

CREATE INDEX payment_created_at IF NOT EXISTS
FOR (p:Payment) ON (p.created_at);

CREATE INDEX payment_owning_lob IF NOT EXISTS
FOR (p:Payment) ON (p.owning_lob);

CREATE INDEX payment_security_event_id IF NOT EXISTS
FOR (e:SecurityEvent) ON (e.payment_id);
