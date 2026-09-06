# Changelog

All notable changes to **Drive Rescue** will be documented in this file.

## [Unreleased]

### Added
- Complete web dashboard with live recovery engine integration
- On-demand RecoveryEngine for raw disk file recovery
- Path traversal protection for all API endpoints
- Thread-safe session management with RLock
- 1MB request body size limit for DoS protection
- Server binding to 127.0.0.1 only for security
- Missing API endpoints: /api/scan_mft, /api/start_recovery, /api/cancel, /api/phase1, /api/phase3, /api/carve, /api/smart, /api/manifest, /api/audit_report, /api/partitions
- RecoveryEngine integration for real file recovery
- SHA-256 forensic hashing for all recovered files

### Fixed
- Fixed RECOVERED_REPAIRED_BLOCK dummy data issue - engine now creates on-demand
- Fixed path traversal in /api/preview_file and /api/preview_meta
- Fixed arbitrary directory creation in /api/open_folder
- Fixed thread safety issues in ACTIVE_SESSION access

## [1.0.0]
- Initial release
- Zero-freeze raw NTFS drive recovery engine
- MFT parser with support for resident and non-resident $DATA attributes
- Signature-based deep file carving for 15+ formats
- S.M.A.R.T. hardware health monitoring
- Multi-pass recovery scheduling
- Mapfile journaling with crash resumability
- Live web dashboard interface
- CLI tool with 11 subcommands
- Native Windows GUI (Tkinter)
