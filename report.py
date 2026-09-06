"""
report.py - Professional Data Recovery Diagnostic & Forensic Audit Certificate Generator

Generates executive-ready HTML & printable recovery certificates detailing
hardware S.M.A.R.T. telemetry, good/bad sector distribution, and itemized file manifests with SHA-256 hashes.
"""

import os
import time
from typing import List, Dict, Any, Optional
from health import SmartHealthReport


def generate_audit_report_html(
    report_path: str,
    drive_info: Dict[str, Any],
    smart_report: Optional[SmartHealthReport],
    stats: Dict[str, Any],
    manifest_records: List[Dict[str, Any]],
):
    """
    Generates a high-quality, printable HTML audit report.
    Compatible across all Python versions (3.8 - 3.13+).
    """
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S UTC", time.gmtime())
    
    tot_files = stats.get("total_files", 0)
    rec_files = stats.get("recovered_files", 0)
    part_files = stats.get("partial_files", 0)
    fail_files = stats.get("failed_files", 0)
    rec_pct = ((rec_files + part_files) / tot_files * 100.0) if tot_files > 0 else 0.0

    smart_verdict = smart_report.health_verdict if smart_report else "UNAVAILABLE"
    smart_color_class = "text-emerald-600 bg-emerald-50 border-emerald-200"
    if smart_report:
        if smart_report.verdict_color == "ROSE":
            smart_color_class = "text-rose-700 bg-rose-50 border-rose-200"
        elif smart_report.verdict_color == "AMBER":
            smart_color_class = "text-amber-700 bg-amber-50 border-amber-200"

    drive_name = str(drive_info.get("name", "Physical Drive"))
    device_path = str(drive_info.get("device_path", "PhysicalDrive"))
    bad_sectors_count = f"{stats.get('bad_sectors', 0):,}"
    realloc_sectors = str(smart_report.reallocated_sectors) if smart_report else "N/A"
    pending_sectors = str(smart_report.pending_sectors) if smart_report else "N/A"
    temp_c = str(smart_report.temperature_c) if smart_report else "N/A"

    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Data Recovery Diagnostic & Forensic Audit Certificate</title>
  <style>
    @media print {
      body { font-size: 11pt; background: #fff !important; color: #000 !important; }
      .no-print { display: none !important; }
      .page-break { page-break-before: always; }
    }
    body {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      line-height: 1.5;
      color: #1e293b;
      background: #f8fafc;
      margin: 0;
      padding: 30px;
    }
    .container {
      max-width: 900px;
      margin: 0 auto;
      background: #ffffff;
      border: 1px solid #e2e8f0;
      border-radius: 12px;
      box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.05);
      padding: 40px;
    }
    .header {
      display: flex;
      justify-content: space-between;
      align-items: flex-start;
      border-bottom: 2px solid #0f172a;
      padding-bottom: 20px;
      margin-bottom: 25px;
    }
    .title { font-size: 20pt; font-weight: 800; color: #0f172a; margin: 0 0 4px 0; }
    .subtitle { font-size: 10pt; color: #64748b; margin: 0; }
    .cert-badge {
      background: #0f172a;
      color: #ffffff;
      padding: 6px 14px;
      border-radius: 6px;
      font-size: 9pt;
      font-weight: 700;
      text-transform: uppercase;
      letter-spacing: 0.5px;
    }
    .grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; margin-bottom: 25px; }
    .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 25px; }
    .card {
      background: #f8fafc;
      border: 1px solid #e2e8f0;
      border-radius: 8px;
      padding: 16px;
    }
    .card-title { font-size: 8pt; font-weight: 700; text-transform: uppercase; color: #64748b; margin-bottom: 6px; }
    .card-value { font-size: 16pt; font-weight: 800; color: #0f172a; font-family: monospace; }
    .section-title {
      font-size: 12pt;
      font-weight: 700;
      color: #0f172a;
      border-bottom: 1px solid #e2e8f0;
      padding-bottom: 6px;
      margin: 25px 0 12px 0;
    }
    table { width: 100%; border-collapse: collapse; font-size: 9pt; margin-top: 10px; }
    th { background: #f1f5f9; text-align: left; padding: 8px 10px; font-weight: 700; color: #334155; border-bottom: 1px solid #cbd5e1; }
    td { padding: 8px 10px; border-bottom: 1px solid #e2e8f0; word-break: break-word; }
    tr:nth-child(even) { background: #f8fafc; }
    .badge {
      display: inline-block;
      padding: 2px 8px;
      border-radius: 4px;
      font-size: 8pt;
      font-weight: 700;
      font-family: monospace;
    }
    .badge-recovered { background: #dcfce7; color: #15803d; }
    .badge-partial { background: #fef3c7; color: #b45309; }
    .badge-failed { background: #fee2e2; color: #b91c1c; }
    .hash { font-family: monospace; font-size: 7.5pt; color: #64748b; }
    .footer { margin-top: 35px; border-top: 1px solid #e2e8f0; padding-top: 15px; font-size: 8pt; color: #94a3b8; text-align: center; }
    .print-btn {
      background: #0284c7;
      color: #ffffff;
      border: none;
      padding: 10px 20px;
      border-radius: 6px;
      font-weight: 700;
      cursor: pointer;
      margin-bottom: 20px;
    }
  </style>
</head>
<body>
  <div class="no-print" style="max-width: 900px; margin: 0 auto 15px auto; text-align: right;">
    <button class="print-btn" onclick="window.print()">Print / Save as PDF Certificate</button>
  </div>

  <div class="container">
    <div class="header">
      <div>
        <h1 class="title">Data Recovery Audit Certificate</h1>
        <p class="subtitle">Direct Overlapped I/O Diagnostic &amp; Extraction Manifest</p>
      </div>
      <div class="cert-badge">Forensic Audit Log</div>
    </div>

    <!-- Hardware & Drive Profile -->
    <div class="grid-2">
      <div class="card">
        <div class="card-title">Source Device Profile</div>
        <div style="font-size: 10pt; font-weight: 600; color: #0f172a; margin-bottom: 4px;">""" + drive_name + """</div>
        <div style="font-size: 9pt; color: #64748b;">Device Path: <code>""" + device_path + """</code></div>
        <div style="font-size: 9pt; color: #64748b;">Filesystem: <strong>NTFS</strong> (Direct $MFT Cluster Extraction)</div>
        <div style="font-size: 9pt; color: #64748b;">Audit Timestamp: """ + timestamp + """</div>
      </div>

      <div class="card">
        <div class="card-title">Hardware S.M.A.R.T. Telemetry</div>
        <div style="font-size: 9pt; font-weight: 700; margin-bottom: 6px;" class="badge """ + smart_color_class + """">""" + smart_verdict + """</div>
        <div style="font-size: 8.5pt; color: #475569;">Reallocated Sectors: <strong>""" + realloc_sectors + """</strong></div>
        <div style="font-size: 8.5pt; color: #475569;">Pending Bad Sectors: <strong>""" + pending_sectors + """</strong></div>
        <div style="font-size: 8.5pt; color: #475569;">Drive Temperature: <strong>""" + temp_c + """ &deg;C</strong></div>
      </div>
    </div>

    <!-- Recovery KPI Statistics -->
    <div class="grid-4">
      <div class="card" style="text-align: center;">
        <div class="card-title">Salvation Rate</div>
        <div class="card-value" style="color: #10b981;">""" + f"{rec_pct:.1f}%" + """</div>
        <div style="font-size: 8pt; color: #64748b;">""" + f"{rec_files + part_files} / {tot_files} Files" + """</div>
      </div>

      <div class="card" style="text-align: center;">
        <div class="card-title">100% Healthy</div>
        <div class="card-value" style="color: #059669;">""" + str(rec_files) + """</div>
        <div style="font-size: 8pt; color: #64748b;">Zero bad sectors</div>
      </div>

      <div class="card" style="text-align: center;">
        <div class="card-title">Partial (0-Filled)</div>
        <div class="card-value" style="color: #d97706;">""" + str(part_files) + """</div>
        <div style="font-size: 8pt; color: #64748b;">Preserved good data</div>
      </div>

      <div class="card" style="text-align: center;">
        <div class="card-title">Bad Sectors Hit</div>
        <div class="card-value" style="color: #dc2626;">""" + bad_sectors_count + """</div>
        <div style="font-size: 8pt; color: #64748b;">Timeouts bypassed</div>
      </div>
    </div>

    <!-- Itemized File Manifest Table -->
    <div class="section-title">Itemized Recovered File Manifest &amp; Cryptographic Hashes</div>
    <table>
      <thead>
        <tr>
          <th style="width: 45%;">File Path</th>
          <th style="width: 15%;">Status</th>
          <th style="width: 12%;">Size</th>
          <th style="width: 28%;">SHA-256 Forensic Hash</th>
        </tr>
      </thead>
      <tbody>
"""

    for rec in manifest_records[:500]:
        status = rec.get("Status", "RECOVERED")
        badge_cls = "badge-recovered" if status == "RECOVERED" else ("badge-partial" if status == "PARTIAL" else "badge-failed")
        size_bytes = rec.get("FileSize", 0)
        size_str = f"{size_bytes/1024:.1f} KB" if size_bytes < 1024*1024 else f"{size_bytes/(1024*1024):.2f} MB"
        sha_hash = rec.get("SHA256", "N/A")
        orig_path = rec.get("OriginalPath", "Unnamed")
        short_hash = f"{sha_hash[:16]}...{sha_hash[-12:]}" if len(sha_hash) > 28 else sha_hash

        html += f"""        <tr>
          <td><strong>{orig_path}</strong></td>
          <td><span class="badge {badge_cls}">{status}</span></td>
          <td>{size_str}</td>
          <td><span class="hash">{short_hash}</span></td>
        </tr>\n"""

    if len(manifest_records) > 500:
        extra_cnt = len(manifest_records) - 500
        html += f"""        <tr>
          <td colspan="4" style="text-align: center; color: #64748b; font-style: italic;">
            ... and {extra_cnt:,} additional records logged in recovery_manifest.csv
          </td>
        </tr>\n"""

    html += """      </tbody>
    </table>

    <div class="footer">
      Generated automatically by Drive Rescue Recovery Engine - Cryptographic SHA-256 Verified
    </div>
  </div>
</body>
</html>
"""

    with open(report_path, "w", encoding="utf-8") as f:
        f.write(html)
