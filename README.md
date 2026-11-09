# Upande Asset

Asset management additions for ERPNext v16: check sheets with root-cause analysis, maintenance service records, workshop materials, asset insurance and an asset dashboard. It uses ERPNext's standard roles only.

## Installation

```bash
cd $PATH_TO_YOUR_BENCH
bench get-app $URL_OF_THIS_REPO --branch Main
bench --site <site> install-app upande_asset
bench --site <site> migrate
```

Migrate creates the doctypes, the custom fields on ERPNext doctypes, the **Insurance Claim Approval** workflow, the **Insurers** supplier group, and the links in the Assets sidebar. The supplier group is created once the ERPNext setup wizard is complete.

## Setup

1. **Asset Settings → Defaults:** one row per company, like Item Defaults:
   - **Workshop parts warehouse:** filled in on Service Record and Asset Repair material rows.
   - **Insurance request warehouse:** used on insurance Material Requests.
   - **Insurance accounts:** Prepaid, Expense, Recoverable, Income and Shortfall. *Recoverable* must be a **Payable** account.

   Warehouses and accounts must belong to the row's company.
2. **Asset Settings → Maintenance:**
   - whether due maintenance creates Service Records
   - whether repairs use Material Requests for their parts
   - how Material Requests and stock issues are created (Draft or Submitted)
3. **Asset Settings → Insurance:**
   - the premium item, a non-stock service item with Deferred Expense
   - renewal alert days, the claim follow-up period, the digest
   - the documents a claim must have
4. **Insurance Policy Type** and **Insurance Incident Type** (Assets → Setup): enable, disable and add your own types (below).
5. Tick **Insurance Required** on the Asset Categories that need cover. New assets copy the flag; tick it on existing assets that need cover.

Every setting has a short explanation behind its ⓘ.

## Check Sheets and Why-Why analysis

- **Templates:** an **Asset Check Sheet Template** per asset category lists the check factors in up to six sections and sets how often the check is due.
- **Results:** a check sheet records each factor as OK or as a fault, with a severity. It is **Passed** or **Needs Attention**. Overdue checks are emailed to the maintenance team daily.
- **Why-Why tab:** found on check sheets and Asset Repairs. You write *What happened*, then a chain of *Because…* answers; the last one is the root cause. Pick a **Cause Type**, then add **Corrective Actions**. Each action becomes a ToDo for the person it is assigned to, and closing the ToDo marks the action Done.
- **Follow-up:** from a sheet you can create an **Asset Repair** (the Why-Why analysis is copied over), an Asset Maintenance or an Insurance Claim. A repair keeps the sheet's status in step: Under Repair, then Repaired.

## Maintenance and Service Records

- **Service Records:** when an ERPNext Asset Maintenance task falls due, a **Pending Service Record** is created and assigned to the task owner (Asset Settings → Maintenance). The task must have a **Service Type**. Service types can suggest Internal or External work, a supplier and a service item.
- **Status:** a record moves between Pending and In Progress. **Complete** submits it, which completes the maintenance log and schedules the next one. A log cannot be completed any other way.
- **Materials:** request parts from stores (Material Issue), or buy parts and services (Purchase → PO → PI). Parts the workshop already holds are recorded with **Issue Used Materials**, if allowed.
- **Costs:** materials are costed at the actual issue value and services from supplier invoices, both in company currency.
- **Locking:** rows already on a Material Request or Stock Entry are locked until that document is cancelled.

## Asset Repair materials

ERPNext's Asset Repair issues its *Stock Items* from the warehouse when the repair is submitted. With **Asset Settings → Maintenance → Use Material Requests for repair parts** turned on:

- **Materials table:** replaces Stock Items on new repairs and works like the Service Record: request from stores, request purchase, or issue used materials.
- **Costs:** issued stock and supplier invoices feed *Total Repair Cost*.
- **Rules:**
  - A repair cannot be completed while stores still owe requested materials.
  - *Capitalize Repair Cost* is not available when materials were requested. ERPNext only capitalises stock the repair issues itself.
- **Connections:** the repair's Connections tab shows its Material Requests, Stock Entries, Purchase Orders, Purchase Invoices, Check Sheet and Insurance Claims.

## Asset insurance

### Policy types

**Insurance Policy Type** comes with the Insurance Regulatory Authority (IRA) classes of general insurance as *Standard* types: Aviation, Engineering, Fire Domestic, Fire Industrial, Liability, Marine, Motor Private, Motor Commercial, Personal Accident, Theft, Workmen's Compensation, Medical and Miscellaneous.

- **Standard types:** can be disabled but not renamed or deleted.
- **Your own types:** add any (e.g. Livestock, Machinery Breakdown) and tag each with its **IRA Class**.
- **Disabled types:** cannot be chosen on new policies. Existing policies and their renewals keep their type.
- **Comprehensive cover:** a type with this ticked marks its assets as comprehensively insured. That cover is preferred when an asset is on several policies.

### Flow

| Step | Where | Result |
|---|---|---|
| Insure assets | Insurance Policy → Submit | The asset's insurance fields are filled from its current policy |
| Pay the premium | Policy → Create → Make Premium Invoice | Purchase Invoice with deferred expense (prepaid → expense), split by cost center if enabled |
| Mid-term change | Policy → Endorse → Add / Remove / Change Sum Insured / Cancel | Pro-rata invoice, debit note or PO; logged, and reversible while the document is a draft |
| Renew | Policy → Create → Raise Renewal Request | Draft renewal policy plus a Material Request or PO. Scrapped, sold and unflagged assets are dropped. The invoice is linked to the renewal policy |
| Claim | Check Sheet → Create → Insurance Claim, or Asset → Create → Insurance Claim | Draft → Recorded → Under Assessment → Approved or Rejected. The policy is picked from the asset, the incident date and the incident type's preferred cover |
| Settle | Claim → Receive Payment / Close with Shortfall | Partly Settled, Settled or Closed |

- **Approving a claim** posts Dr Claim Recoverable (insurer) / Cr Recovery Income. For a Total Loss it credits the disposal account instead; scrap the asset first.
- **Receipts and shortfalls** post on account against the insurer. Use Payment Reconciliation to match them.
- **Amounts** on policies and claims are always in the company's currency.

### Other cover

- **Non-asset cover:** set **Insurance For = Other** for cover that is not tied to an asset, such as medical, WIBA or public liability. Enter what is insured, the sum insured and a cost center. Endorsements are limited to Change Sum Insured and Cancel Policy.
- **Overlapping cover:** an asset cannot be on two policies of the same type on the same day. To move it mid-term, endorse it off the old policy first. Different types (e.g. Motor and Machinery Breakdown) may run side by side.
- **Policies bought before go-live:** tick **Is Opening** and enter the **Go-Live Date** and **Prepaid Balance at Go-Live**, then use **Create → Generate Release Schedule**. One Journal Entry is created per month; past months are submitted at once and the rest are submitted by the daily scheduler.
- **Importing policies:** use Data Import on Insurance Policy with the **Insured Assets** child table: one row per asset, policy columns only on the first row. Import as drafts, review, then submit.

## Asset Dashboard

`/asset-dashboard` (linked at the top of the Assets sidebar) gives a summarised, clickable view of the assets in one company:

- **Overview:** assets, book value, insurance cover, open jobs and claims, what needs attention, and activity in the chosen period.
- **Schedules:** a calendar of renewals, service jobs, maintenance tasks and ToDos. Month view is the default; Week and Day views show who each item is assigned to. Filter by typing a person's name.
- **Maintenance & Service** and **Insurance:** jobs, costs, check sheets, repairs, policies, renewals and claims.

**Filters:** an accounting dimension (Cost Center and any enabled dimension except Project) and a period, from this week to last year or a custom range.

**Drill-down:** clicking any figure or chart opens a pop-up of the records behind it, with a link to the full list. The logo returns to the desk.

## Reports

Insurance Register, Uninsured Assets, Insurance Claims Register (with loss ratio per insurer), Premium Analysis, Asset Incident History, Check Sheet History, Check Sheet Fault Analysis and Check Sheet Compliance.

Every report filters by:
- **Company:** required.
- **Location.**
- **Cost Center and each enabled accounting dimension.**

A parent location or cost center includes everything under it. Premium Analysis counts a policy's premium at a location in proportion to the sum insured held there.

## Scheduled jobs

- **Daily:**
  - policy status changes: Active → Expiring → Expired / Renewed
  - renewal alerts to the owner, the chosen roles and the broker
  - follow-ups on claims waiting on the insurer
  - due release-schedule entries are submitted
  - a ToDo when an insured asset is scrapped or sold
  - overdue check sheet emails
- **Insurance digest:** uninsured and under-insured assets, at the frequency set in Asset Settings.

## Roles

Only ERPNext's standard roles are used: Accounts User / Manager, Maintenance User / Manager, Stock and Purchase roles, and System Manager. Grant other roles access with the Role Permission Manager. If they should move claims between states, also add them to the *Insurance Claim Approval* workflow.

ERPNext gives Asset Repair to the Quality Manager and Manufacturing Manager roles only. Give one of these to whoever completes repairs.

## Contributing

This app uses `pre-commit` (ruff, eslint, prettier, pyupgrade):

```bash
cd apps/upande_asset
pre-commit install
```

## License

MIT
