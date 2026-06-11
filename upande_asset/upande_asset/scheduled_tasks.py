#
# Checks for overdue asset check sheets and notifies configured recipients.

import frappe
from frappe.utils import today, add_days, getdate


FREQ_DAYS = {
    "Daily": 1,
    "Weekly": 7,
    "Monthly": 30,
    "Quarterly": 90,
    "Yearly": 365,
}


def check_missed_asset_checks():
    """Daily scheduler job.

    For every enabled Asset Check Sheet Schedule, check whether the asset
    has a submitted check sheet within the required frequency window.
    If overdue, email all configured notification recipients (falling back to
    the template's maintenance team manager when none are configured).

    Also refreshes the schedule record's status fields so the list view
    always shows current data without manual saves.
    """
    schedules = frappe.get_all(
        "Asset Check Sheet Schedule",
        filters={"enabled": 1},
        fields=["name", "asset", "asset_name", "check_sheet_template"],
    )

    for sched in schedules:
        try:
            _process_schedule(sched)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Check sheet schedule error: {sched['name']}",
            )


# ── Per-schedule logic ─────────────────────────────────────────────────────────

def _process_schedule(sched):
    tmpl = frappe.get_doc("Asset Check Sheet Template", sched["check_sheet_template"])

    freq_days = (
        int(tmpl.custom_frequency_days or 0)
        if tmpl.frequency == "Custom"
        else FREQ_DAYS.get(tmpl.frequency, 0)
    )
    if not freq_days:
        return

    last = frappe.get_all(
        "Asset Check Sheet",
        filters={
            "asset": sched["asset"],
            "check_sheet_template": sched["check_sheet_template"],
            "docstatus": 1,
        },
        fields=["check_date"],
        order_by="check_date desc",
        limit=1,
    )

    last_date = getdate(last[0]["check_date"]) if last else None
    due = add_days(last_date, freq_days) if last_date else getdate(today())
    days_overdue = max(0, (getdate(today()) - getdate(due)).days)
    is_overdue = getdate(today()) >= due

    # Update schedule record status fields
    frappe.db.set_value(
        "Asset Check Sheet Schedule",
        sched["name"],
        {
            "last_check_date": str(last_date) if last_date else None,
            "next_due_date":   str(due),
            "check_status":    "Overdue" if is_overdue else "Up to Date",
            "days_overdue":    days_overdue if is_overdue else 0,
        },
    )

    if not is_overdue:
        return

    recipients = _get_recipients(sched, tmpl)
    if not recipients:
        return

    _send_notification(sched, tmpl, last_date, due, days_overdue, recipients)


def _get_recipients(sched, tmpl):
    """Return list of email addresses to notify.

    Uses the schedule's explicit recipient list first;
    falls back to the template's maintenance team manager.
    """
    emails = []

    rows = frappe.get_all(
        "Check Sheet Notification Recipient",
        filters={"parent": sched["name"], "parenttype": "Asset Check Sheet Schedule"},
        fields=["email", "user"],
    )
    for row in rows:
        addr = row.get("email") or frappe.db.get_value("User", row["user"], "email")
        if addr:
            emails.append(addr)

    if not emails and tmpl.asset_maintenance_team:
        mgr_email = _get_team_manager_email(tmpl.asset_maintenance_team)
        if mgr_email:
            emails.append(mgr_email)

    return list(dict.fromkeys(emails))  # deduplicate, preserve order


def _send_notification(sched, tmpl, last_date, due, days_overdue, recipients):
    asset_link = f'<a href="/app/asset/{sched["asset"]}">{sched["asset"]}</a>'
    freq_label  = (
        f"Every {tmpl.custom_frequency_days or '?'} days"
        if tmpl.frequency == "Custom"
        else tmpl.frequency
    )
    last_str = str(last_date) if last_date else "Never"

    subject = (
        f"[Overdue] {freq_label} check missed — "
        f"{sched['asset_name'] or sched['asset']} ({days_overdue} day(s))"
    )

    message = f"""
<p>A scheduled asset check sheet has not been completed on time.</p>
<table style="border-collapse:collapse;width:100%;max-width:480px">
  <tr><td style="padding:6px 12px;background:#f3f4f6;font-weight:600">Asset</td>
      <td style="padding:6px 12px">{asset_link}</td></tr>
  <tr><td style="padding:6px 12px;background:#f3f4f6;font-weight:600">Template</td>
      <td style="padding:6px 12px">{tmpl.name}</td></tr>
  <tr><td style="padding:6px 12px;background:#f3f4f6;font-weight:600">Frequency</td>
      <td style="padding:6px 12px">{freq_label}</td></tr>
  <tr><td style="padding:6px 12px;background:#f3f4f6;font-weight:600">Last Check</td>
      <td style="padding:6px 12px">{last_str}</td></tr>
  <tr><td style="padding:6px 12px;background:#f3f4f6;font-weight:600">Was Due By</td>
      <td style="padding:6px 12px">{due}</td></tr>
  <tr><td style="padding:6px 12px;background:#fee2e2;font-weight:600;color:#7f1d1d">Days Overdue</td>
      <td style="padding:6px 12px;background:#fee2e2;color:#7f1d1d"><b>{days_overdue}</b></td></tr>
</table>
<p style="margin-top:16px">Please ensure a check sheet is completed as soon as possible.</p>
<p><a href="/app/asset-check-sheet/new-asset-check-sheet-1">Create Check Sheet</a>
   &nbsp;|&nbsp;
   <a href="/checksheet">Mobile Check Sheet</a></p>
"""

    frappe.sendmail(
        recipients=recipients,
        subject=subject,
        message=message,
    )


def _get_team_manager_email(team_name):
    try:
        mgr = frappe.db.get_value("Asset Maintenance Team", team_name, "maintenance_manager")
        if mgr:
            return frappe.db.get_value("User", mgr, "email")
    except Exception:
        pass
    return None
