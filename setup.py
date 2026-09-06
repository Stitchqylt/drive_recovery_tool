#!/usr/bin/env python3
"""
setup.py - Standard setup script for Drive Rescue.
Enables `pip install .` and `pip install -e .` across all Python environments.
"""

import os
try:
    from setuptools import setup, find_packages
except ImportError:
    from distutils.core import setup
    find_packages = lambda: []

here = os.path.abspath(os.path.dirname(__file__))

with open(os.path.join(here, "README.md"), encoding="utf-8") as f:
    long_description = f.read()

setup(
    name="drive-rescue",
    version="1.0.0",
    description="Zero-Freeze, Non-Destructive Raw Physical Drive & NTFS Live File Recovery Engine",
    long_description=long_description,
    long_description_content_type="text/markdown",
    author="Drive Rescue Contributors",
    author_email="support@drive-rescue.dev",
    url="https://github.com/drive-rescue/drive-rescue",
    license="MIT",
    py_modules=[
        "__init__",
        "__main__",
        "main",
        "diskio",
        "partitions",
        "ntfs",
        "engine",
        "recovery_map",
        "monitor",
        "multipass_scheduler",
        "carver",
        "health",
        "hash_verify",
        "report",
        "cli",
        "gui",
        "dashboard",
    ],
    include_package_data=True,
    package_data={
        "": ["dashboard.html", "*.png", "*.ico", "*.md", "*.bat", "*.sh"],
    },
    install_requires=[],
    python_requires=">=3.8",
    entry_points={
        "console_scripts": [
            "drive-rescue = main:main",
            "drive-recovery = main:main",
        ],
    },
    classifiers=[
        "Development Status :: 5 - Production/Stable",
        "Environment :: Win32 (MS Windows)",
        "Environment :: MacOS X",
        "Environment :: Console",
        "Environment :: Web Environment",
        "Intended Audience :: System Administrators",
        "Intended Audience :: Developers",
        "Topic :: System :: Recovery Tools",
        "Topic :: System :: Filesystems",
        "License :: OSI Approved :: MIT License",
        "Programming Language :: Python :: 3",
        "Programming Language :: Python :: 3.8",
        "Programming Language :: Python :: 3.9",
        "Programming Language :: Python :: 3.10",
        "Programming Language :: Python :: 3.11",
        "Programming Language :: Python :: 3.12",
        "Programming Language :: Python :: 3.13",
    ],
)
