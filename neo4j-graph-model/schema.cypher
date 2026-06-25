// Constraints — one node per business key
CREATE CONSTRAINT instruction_id_unique IF NOT EXISTS
FOR (i:Instruction) REQUIRE i.instruction_id IS UNIQUE;

CREATE CONSTRAINT instruction_version_key_unique IF NOT EXISTS
FOR (v:InstructionVersion) REQUIRE v.version_key IS UNIQUE;

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
