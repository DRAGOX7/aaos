# Phase 9 limitations

- Phase 9 performs zero real external writes and adds no provider integration.
- The Google Calendar CREATE is historical Phase 6B evidence only.
- Local model identifiers and Phase 8 revisions remain environment-specific;
  this release qualification does not download or load models.
- Performance values from fake adapters are not model or provider latency.
- Phase 8 local model lifecycles are sequential on the 6 GB VRAM target; this
  package does not claim simultaneous BGE-M3, Qwen, NLI, or VLM residency.
- RLM, Deep Ask AAOS, recursive delegation, and unrestricted computation are
  future research, not implemented functionality.
- Connected Moodle setup and local credentials remain operator-specific and
  are never included in public evidence.
- Phase 9 remains `IN PROGRESS` until its own human acceptance and freeze gate.
