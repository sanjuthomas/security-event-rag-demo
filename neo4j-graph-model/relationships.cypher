// Neo4j graph model — instructions and security events
// Documentation only. No data is created by this file.
//
// Source of truth in MongoDB:
//   ssi_cash_instructions.instructions
//   security_events.instruction-lifecycle-manager
//
// ---------------------------------------------------------------------------
// NODE LABELS
// ---------------------------------------------------------------------------
//
// (:Instruction)
//   instruction_id          unique business id (UUID)
//
// (:InstructionVersion)
//   version_key             unique "{instruction_id}:{version_number}" (Community Edition)
//   instruction_id
//   version_number
//   status                  DRAFT | PENDING | STANDING | SINGLE_USE | SUSPENDED | REJECTED | USED | EXPIRED | DELETED
//   instruction_type        STANDING | SINGLE_USE
//   wire_scope              DOMESTIC | INTERNATIONAL
//   owning_lob              FICC | FX | DESK_*
//   currency                ISO 4217 route currency
//   effective_date
//   end_date
//   valid_in                version valid-from (Mongo "in")
//   valid_out               version valid-to (Mongo "out", null = current)
//   usage_count
//
// (:SecurityEvent)
//   event_id                unique UUID
//   timestamp
//   severity                INFO | ALERT | ...
//   message
//   action                  CREATE | SUBMIT | APPROVE | VIEW | ...
//   outcome                 success | failure
//   event_type              JSON array string (ECS-style types)
//   reason                  policy denial reason when present
//   source_application
//   source_version
//
// (:User)
//   user_id                 unique ZITADEL / seed user id
//   title                   Analyst | Associate | Vice President | ...
//   lob                     profit center when applicable
//   roles                   JSON array string
//   supervisor_id
//
// (:ProfitCenter)
//   lob                     unique FICC | FX | DESK_*
//
// ---------------------------------------------------------------------------
// RELATIONSHIP TYPES
// ---------------------------------------------------------------------------
//
// (:Instruction)-[:HAS_VERSION]->(:InstructionVersion)
//   Logical instruction to each point-in-time version.
//
// (:InstructionVersion)-[:SUPERSEDES]->(:InstructionVersion)
//   Newer version replaces the previous version_number - 1.
//
// (:InstructionVersion)-[:OWNED_BY]->(:ProfitCenter)
//   Maps instruction.owning_lob to a profit center node.
//
// (:User)-[:CREATED]->(:InstructionVersion)
//   From instruction.created_by on the version payload.
//
// (:User)-[:APPROVED]->(:InstructionVersion)
//   From instruction.approved_by when present.
//
// (:User)-[:REJECTED]->(:InstructionVersion)
//   From instruction.rejected_by when present.
//
// (:User)-[:REPORTS_TO]->(:User)
//   From actor/creator supervisor_id hierarchy.
//
// (:User)-[:ACTED_AS]->(:SecurityEvent)
//   Security event actor (subject who performed the action).
//
// (:SecurityEvent)-[:TARGETS]->(:Instruction)
//   From security_event.resource.id.
//
// (:SecurityEvent)-[:TARGETS_VERSION]->(:InstructionVersion)
//   When security_event.resource.version_number is set.
//
// (:SecurityEvent)-[:INVOLVES_LOB]->(:ProfitCenter)
//   From security_event.resource.owning_lob.
