# Security Policy

## Reporting a Vulnerability

If you discover a potential security vulnerability in **Drive Rescue**, please do not open a public GitHub issue. Instead, email our security team directly at **security@drive-rescue.dev**.

Please include a description of the issue, the affected code, and any steps to reproduce it. We will respond within 48 hours and coordinate a fix and disclosure timeline.

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 1.0.x   | :white_check_mark: |

## Security Features

- Path traversal protection on all API endpoints
- Request body size limiting (1MB)
- Server binding to localhost only
- Thread-safe session management
- Non-destructive read-only operations
- SHA-256 forensic integrity verification
