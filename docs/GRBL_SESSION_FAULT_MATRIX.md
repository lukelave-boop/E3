# Primary controller fault matrix

This matrix is the human-readable index for the deterministic `FAULT_MATRIX` in
`tests/test_controller_session.py`. “Automated” means fake-transport or state-model
coverage on Windows unless the owner is marked POSIX. It is not evidence that real
hardware has been tested. The physical 20-cycle acceptance run remains required.

| # | Scenario | Verification owner | Evidence |
|---:|---|---|---|
| 1 | Clean startup/handshake | `test_connect_publishes_only_after_full_grbl_handshake` | Automated |
| 2 | Complete startup chatter | `test_primary_synchronization_discards_chatter_arriving_during_settle` | Automated |
| 3 | Unterminated startup bytes | `test_synchronize_input_discards_queued_partial_and_kernel_rx_bytes` | POSIX CI |
| 4 | Invalid UTF-8 startup bytes | `test_synchronize_input_discards_queued_partial_and_kernel_rx_bytes`, `test_posix_serial_invalid_utf8_after_synchronization_latches_fault` | POSIX CI startup discard and post-sync fault |
| 5 | Bytes during settle | `test_primary_synchronization_discards_chatter_arriving_during_settle` | Automated |
| 6 | Kernel RX bytes | `test_synchronize_input_discards_queued_partial_and_kernel_rx_bytes` | POSIX CI |
| 7 | Delayed prior-command `ok` | `test_physical_dollar_hash_failure_sequence_has_all_seven_outcomes` | Automated transcript |
| 8 | Delayed prior-generation `ok` | `test_delayed_old_generation_ack_cannot_complete_new_dollar_hash` | Automated |
| 9 | Duplicate `ok` | `test_millisecond_delayed_duplicate_ack_is_rejected_before_next_command` | Automated |
| 10 | Interleaved status | `test_realtime_status_is_diverted_from_command_payload` | Automated |
| 11 | Mid-handshake banner | `test_startup_or_malformed_frame_quarantines_and_clears_pending_transaction` | Automated |
| 12 | Consumed `error:x` | `test_consumed_error_and_alarm_do_not_poison_session` | Automated |
| 13 | Consumed `ALARM:x` | `test_consumed_error_and_alarm_do_not_poison_session` | Automated |
| 14 | Exact pre-Home `error:9` | `test_exact_pre_home_error_9_uses_only_bounded_unlock_and_still_requires_home` | Automated |
| 15 | Other rejection | `test_non_error_9_pre_home_rejection_never_unlocks_or_publishes` | Automated |
| 16 | Pre-byte write failure | `test_failed_or_partial_write_quarantines_exact_session` | Automated |
| 17 | Partial write | `test_failed_or_partial_write_quarantines_exact_session` | Automated |
| 18 | Read failure | `test_read_failure_quarantines_exact_session_and_records_command` | Automated |
| 19 | `$I` timeout | `test_overall_connect_deadline_clamps_identity_transaction` | Automated |
| 20 | `$$` timeout | `test_connect_stage_faults_fail_closed` | Automated |
| 21 | `$$` lacks `$1` | `test_connect_stage_faults_fail_closed` | Automated |
| 22 | Stale `$1=255` | `test_stale_step_idle_hold_is_repaired_and_verified_before_publication` | Automated |
| 23 | `$G` timeout | `test_connect_stage_faults_fail_closed` | Automated |
| 24 | `$#` timeout | `test_physical_dollar_hash_failure_sequence_has_all_seven_outcomes` | Automated transcript |
| 25 | `$#` lacks workspace | `test_incomplete_coordinate_payload_never_publishes_candidate` | Automated |
| 26 | `$#` lacks G92 | `test_incomplete_coordinate_payload_never_publishes_candidate` | Automated |
| 27 | Realtime timeout | `test_invalid_realtime_handshake_never_publishes_candidate` | Automated |
| 28 | Malformed realtime | `test_invalid_realtime_handshake_never_publishes_candidate` | Automated |
| 29 | STOP during ACK wait | `test_stop_while_waiting_for_ack_quarantines_and_recovers_fresh_session` | Automated |
| 30 | STOP during write gate | `test_stop_stays_bounded_while_write_gate_is_occupied` | Automated |
| 31 | STOP during Home | `test_stop_during_homing_quarantines_old_generation_without_ready_publish` | Automated |
| 32 | STOP during stream | `test_stop_during_active_job_cancels_stream_without_receipt` | Automated transcript |
| 33 | STOP before completion | `test_stop_during_final_ack_cannot_publish_success_receipt` | Automated |
| 34 | Reconnect while worker unwinds | `test_reconnect_can_publish_while_old_job_worker_unwinds` | Deterministic race test |
| 35 | Stale cleanup close | `test_stale_job_cleanup_cannot_close_replacement_transport` | Deterministic exact-transport ownership test |
| 36 | Stale worker write | `test_stale_job_worker_cannot_write_replacement_transport` | Deterministic exact-session ownership test |
| 37 | Repeated Reconnect | `test_two_clients_share_one_replacement_and_stale_disconnect_is_rejected` | Pi RPC automated |
| 38 | Connect/Reconnect race | `test_connect_and_reconnect_race_share_one_published_session` | Automated |
| 39 | Reconnect/STOP race | `test_stop_cancels_reconnect_candidate_before_it_can_publish` | Automated |
| 40 | Home/poll race | `test_home_and_status_poll_are_coherent_and_two_home_requests_exclude` | Automated |
| 41 | Concurrent Home | `test_home_and_status_poll_are_coherent_and_two_home_requests_exclude` | Automated |
| 42 | Two desktop clients | `test_two_clients_share_one_replacement_and_stale_disconnect_is_rejected` | Pi RPC automated |
| 43 | Client closes during job | `test_authenticated_client_disconnect_does_not_stop_accepted_powered_job` | Pi RPC automated |
| 44 | USB disappears idle | `test_read_failure_quarantines_exact_session_and_records_command` | Simulated; physical pending |
| 45 | USB disappears in job | `test_pi_local_controller_failure_persists_failed_without_auto_retry` | Simulated; physical pending |
| 46 | Same by-id reappears | `test_twenty_stop_recover_home_cycles_use_fresh_generations` | Simulated; physical pending |
| 47 | Wrong controller at path | `test_wrong_controller_identity_at_configured_path_fails_closed` | Automated |
| 48 | Pi restart | `test_pi_restart_marks_persisted_running_job_interrupted_without_resume` | Automated |
| 49 | Windows reconnect | `test_explicit_reconnect_disconnects_then_connects_without_motion_or_home` | Desktop automated |
| 50 | Shutdown during recovery | `test_shutdown_during_recovery_prevents_candidate_publication` | Automated |
| 51 | Secondary fault isolation | `test_secondary_fault_status_cannot_frame_primary_transaction` | Automated |
| 52 | Secondary synchronize regression | `test_initial_open_applies_startup_delay_and_synchronizes_before_exact_off` | POSIX CI |
| 53 | Recovery emits no motion/laser | `test_physical_dollar_hash_failure_sequence_has_all_seven_outcomes` | Automated transcript |
| 54 | No job auto-resume | `test_pi_restart_marks_persisted_running_job_interrupted_without_resume` | Automated |
| 55 | Home exact generation | `test_home_is_generation_gated_and_rejected_when_already_motion_ready` | Automated |
| 56 | No contradictory UI | `test_reconnect_and_disconnected_states_cannot_claim_motion_ready` | Desktop automated |
| 57 | Recovery reaches Home Required | `test_twenty_stop_recover_home_cycles_use_fresh_generations` | Automated |
| 58 | Recovery failure actionable | `test_recovery_failure_is_actionable_reconnect_required` | Automated |
| 59 | Explicit reconnect after failure | `test_explicit_reconnect_after_recovery_failure` | Automated |
| 60 | Twenty recovery cycles | `test_twenty_stop_recover_home_cycles_use_fresh_generations` | Deterministic automated; physical pending |

The seeded 1,000-lifecycle soak is owned by
`test_seeded_1000_controller_session_lifecycle_soak`.
Linux pseudoterminal evidence is produced by the focused Ubuntu jobs in both CI
workflows. Results must be recorded in `CURRENT_STATE.md`; neither CI nor simulation
is a substitute for the operator run in `GRBL_SESSION_RECOVERY_VALIDATION.md`.
