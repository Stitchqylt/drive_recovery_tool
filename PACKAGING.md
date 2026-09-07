# Packaging & Architecture Guide

This repository now provides a package entry point `drive_rescue` in addition to the existing flat top-level modules. The refactor was performed incrementally so both import styles work during the transition.

## Key Points
- **Package name**: `drive-rescue` (importable as `drive_rescue`)
- **Stable console entry points**: `drive-rescue` and `drive-recovery` mapped to `drive_rescue.main:main` (in `setup.py` & `pyproject.toml`)
- Large `*.img` and `*.raw` files are recommended to be tracked via Git LFS; a `.gitattributes` file is configured.

## LFS Migration
If you want to migrate existing `.img` files into LFS (rewriting history), coordinate with contributors before running `git lfs migrate import`.
