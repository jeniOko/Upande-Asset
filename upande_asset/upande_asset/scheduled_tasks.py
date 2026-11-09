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

    from upande_asset.upande_asset.emails import Batch

    # One email per recipient a day, listing every overdue check (not one email per asset).
    batch = Batch("Asset Check Sheet Schedule",
        single=lambda i: _overdue_pair(check_sheet_overdue_email(**i)),
        grouped=check_sheet_group_email,
        send_email=lambda recipients, subject, message: frappe.sendmail(recipients=recipients, subject=subject, message=message))
    for sched in schedules:
        try:
            _process_schedule(sched, batch)
        except Exception:
            frappe.log_error(
                frappe.get_traceback(),
                f"Check sheet schedule error: {sched['name']}",
            )
    batch.send()


def _overdue_pair(email):
    return email["subject"], email["message"]


# ── Per-schedule logic ─────────────────────────────────────────────────────────

def _process_schedule(sched, batch=None):
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
    # Never checked: the first check was due when the schedule was set up. (Counting from
    # "today" made the due date move every day, so the overdue days never grew.)
    if last_date:
        due = add_days(last_date, freq_days)
    else:
        due = getdate(frappe.db.get_value("Asset Check Sheet Schedule", sched["name"], "creation"))
    # Due today is not overdue yet: the check can still be done today.
    days_overdue = max(0, (getdate(today()) - getdate(due)).days)
    is_overdue = days_overdue > 0

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

    if batch is None:
        _send_notification(sched, tmpl, last_date, due, days_overdue, recipients)
    else:
        batch.add(sched["name"], {"sched": sched, "tmpl": tmpl, "last_date": last_date, "due": due,
            "days_overdue": days_overdue}, emails=recipients)


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
    frappe.sendmail(recipients=recipients, **check_sheet_overdue_email(sched, tmpl, last_date, due, days_overdue))


def check_sheet_overdue_email(sched, tmpl, last_date, due, days_overdue):
    """Overdue check sheet, in the shared reminder layout (absolute links, site date format)."""
    from frappe import _
    from frappe.utils import escape_html, get_url, get_url_to_form

    from upande_asset.upande_asset import emails as em

    freq_label = (
        _("Every {0} days").format(tmpl.custom_frequency_days or "?")
        if tmpl.frequency == "Custom"
        else _(tmpl.frequency)
    )
    asset_label = sched["asset_name"] or sched["asset"]
    new_sheet = get_url(f"/desk/asset-check-sheet/new?asset={frappe.utils.quote(sched['asset'])}"
        f"&check_sheet_template={frappe.utils.quote(tmpl.name)}")
    message = em.card(
        area=_("Check Sheets"), tone="danger", status=_("Overdue by {0} days").format(days_overdue),
        title=_("{0} check missed: {1}").format(freq_label, asset_label),
        intro=escape_html(_("The scheduled check for this asset has not been done. Complete a check sheet as soon as possible.")),
        facts=[
            (_("Asset"), em.form_link("Asset", sched["asset"], f"{asset_label} ({sched['asset']})")),
            (_("Check sheet"), escape_html(tmpl.name)),
            (_("How often"), escape_html(freq_label)),
            (_("Last check"), em.date(last_date) if last_date else _("Never")),
            (_("Was due by"), em.date(due)),
        ],
        button=(_("Start check sheet"), new_sheet),
        company=frappe.db.get_value("Asset", sched["asset"], "company"),
        links=[(_("Mobile check sheet"), get_url("/checksheet")),
            (_("Check sheet history"), get_url_to_form("Asset", sched["asset"]))],
    )
    subject = _("Overdue: {0} check missed for {1} ({2} days)").format(freq_label, asset_label, days_overdue)
    return {"subject": subject, "message": message}


def _get_team_manager_email(team_name):
    try:
        mgr = frappe.db.get_value("Asset Maintenance Team", team_name, "maintenance_manager")
        if mgr:
            return frappe.db.get_value("User", mgr, "email")
    except Exception:
        pass
    return None


def check_sheet_group_email(items):
    from frappe import _
    from frappe.utils import escape_html, get_url, get_url_to_list

    from upande_asset.upande_asset import emails as em

    items = sorted(items, key=lambda i: -i["days_overdue"])
    rows = [[
        em.form_link("Asset", i["sched"]["asset"], i["sched"]["asset_name"] or i["sched"]["asset"]),
        escape_html(i["tmpl"].name),
        em.date(i["last_date"]) if i["last_date"] else _("Never"),
        em.date(i["due"]),
        em.pill(_("{0} days").format(i["days_overdue"]), "danger"),
    ] for i in items]
    subject = _("{0} asset checks are overdue").format(len(items))
    message = em.grouped(
        area=_("Check Sheets"), tone="danger", title=_("{0} asset checks are overdue").format(len(items)),
        status=_("Most overdue {0} days").format(items[0]["days_overdue"]),
        intro=escape_html(_("These scheduled checks have not been done. Complete a check sheet for each asset as soon as possible.")),
        headers=[_("Asset"), _("Check sheet"), _("Last check"), _("Was due by"), _("Overdue")], rows=rows,
        button=(_("Open check sheet schedules"), get_url_to_list("Asset Check Sheet Schedule")),
        company=frappe.db.get_value("Asset", items[0]["sched"]["asset"], "company"),
    )
    return subject, message
