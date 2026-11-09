// Copyright (c) 2026, jeniffer@upande.com and contributors
// For license information, please see license.txt

const POLICY_API = "upande_asset.upande_asset.doctype.insurance_policy.insurance_policy";

frappe.ui.form.on("Insurance Policy", {
	setup(frm) {
		frm.set_query("policy_type", () => ({ filters: { enabled: 1 } }));
		frm.set_query("asset", "insured_assets", (doc) => ({
			query: `${POLICY_API}.get_insurable_assets`,
			filters: {
				company: doc.company,
				exclude: (doc.insured_assets || []).filter((r) => r.row_status === "Active" && r.asset).map((r) => r.asset),
			},
		}));
		frm.set_query("insurer", () => ({ filters: { disabled: 0 } }));
		frm.set_query("broker", () => ({ filters: { disabled: 0 } }));
		frm.set_query("opening_journal_entry", (doc) => ({ filters: { company: doc.company, docstatus: 1 } }));
		frm.set_query("cost_center", (doc) => ({ filters: { company: doc.company, is_group: 0 } }));
		// Address & Contact tab – same queries ERPNext uses on a Purchase Order.
		frm.set_query("supplier_address", (doc) => ({
			query: "frappe.contacts.doctype.address.address.address_query",
			filters: { link_doctype: "Supplier", link_name: doc.insurer },
		}));
		frm.set_query("contact_person", (doc) => ({
			query: "frappe.contacts.doctype.contact.contact.contact_query",
			filters: { link_doctype: "Supplier", link_name: doc.insurer },
		}));
		["billing_address", "dispatch_address", "shipping_address"].forEach((f) => {
			frm.set_query(f, (doc) => ({
				query: "frappe.contacts.doctype.address.address.address_query",
				filters: { link_doctype: "Company", link_name: doc.company },
			}));
		});
	},


	insurance_for(frm) {
		if (frm.doc.insurance_for === "Other" && (frm.doc.insured_assets || []).length) {
			frappe.confirm(__("Non-asset policies do not list insured assets. Remove the {0} asset row(s)?", [frm.doc.insured_assets.length]),
				() => { frm.clear_table("insured_assets"); frm.refresh_field("insured_assets"); },
				() => frm.set_value("insurance_for", "Assets"));
		}
	},

	insurer(frm) {
		if (!frm.doc.insurer) {
			frm.set_value({ supplier_address: "", address_display: "", contact_person: "", contact_display: "", contact_mobile: "", contact_email: "" });
			return;
		}
		frappe.xcall(`${POLICY_API}.get_insurer_details`, { insurer: frm.doc.insurer }).then((r) => frm.set_value(r));
	},

	supplier_address: (frm) => set_address_display(frm, "supplier_address", "address_display"),
	dispatch_address: (frm) => set_address_display(frm, "dispatch_address", "dispatch_address_display"),
	shipping_address: (frm) => set_address_display(frm, "shipping_address", "shipping_address_display"),
	billing_address: (frm) => set_address_display(frm, "billing_address", "billing_address_display"),

	contact_person(frm) {
		if (!frm.doc.contact_person) {
			frm.set_value({ contact_display: "", contact_mobile: "", contact_email: "" });
			return;
		}
		frappe.db.get_value("Contact", frm.doc.contact_person, ["full_name", "mobile_no", "phone", "email_id"]).then((r) => {
			const c = r.message || {};
			frm.set_value({ contact_display: c.full_name, contact_mobile: c.mobile_no || c.phone, contact_email: c.email_id });
		});
	},

	tc_name(frm) {
		if (!frm.doc.tc_name) return;
		frappe.xcall("erpnext.setup.doctype.terms_and_conditions.terms_and_conditions.get_terms_and_conditions", {
			template_name: frm.doc.tc_name,
			doc: frm.doc,
		}).then((terms) => frm.set_value("terms", terms || ""));
	},

	basic_premium: (frm) => set_total(frm),
	levies: (frm) => set_total(frm),

	async refresh(frm) {
		if (frm.is_new()) return;
		const ctx = await frappe.xcall(`${POLICY_API}.get_policy_context`, { policy: frm.doc.name });
		const live = frm.doc.docstatus === 1 && ["Active", "Expiring"].includes(frm.doc.status);

		// ── Draft: new cover with no history ────────────────────────────────
		if (frm.doc.docstatus === 0 && ctx.is_accounts_user && !ctx.open_procurement) {
			frm.add_custom_button(__("Raise Purchase Request"), () => raise_procurement(frm, ctx, "raise_purchase_request"));
		}

		if (frm.doc.docstatus !== 1) return;

		// ── Premium invoice ────────────────────────────────────────────────
		if (frm.doc.is_opening !== "Yes" && !frm.doc.purchase_invoice && !ctx.premium_invoiced && !ctx.open_procurement
			&& ctx.is_accounts_user && frm.doc.status !== "Cancelled") {
			frm.add_custom_button(__("Make Premium Invoice"), () => {
				frappe.xcall(`${POLICY_API}.make_premium_invoice`, { policy: frm.doc.name }).then((name) => {
					frappe.set_route("Form", "Purchase Invoice", name);
				});
			}, __("Create"));
		}
		if (ctx.draft_premium_pi) {
			frm.add_custom_button(__("Draft Premium Invoice"), () => frappe.set_route("Form", "Purchase Invoice", ctx.draft_premium_pi), __("View"));
		}

		// ── Opening policy release schedule ────────────────────────────────
		if (frm.doc.is_opening === "Yes" && flt(frm.doc.prepaid_balance_at_go_live) > 0 && !frm.doc.release_schedule_generated
			&& ctx.is_accounts_manager) {
			frm.add_custom_button(__("Generate Release Schedule"), () => {
				frappe.confirm(
					__("Create monthly Journal Entries releasing {0} from prepaid to insurance expense? Entries dated up to today are submitted; later ones stay as drafts and are submitted automatically when due.",
						[format_currency(frm.doc.prepaid_balance_at_go_live, erpnext.get_currency(frm.doc.company))]),
					() => frappe.xcall(`${POLICY_API}.generate_release_schedule`, { policy: frm.doc.name }).then((names) => {
						frappe.show_alert({ message: __("{0} Journal Entries created", [names.length]), indicator: "green" });
						frm.reload_doc();
					})
				);
			}, __("Create"));
		}

		// ── Renewal ─────────────────────────────────────────────────────────
		if (["Active", "Expiring", "Expired"].includes(frm.doc.status) && !frm.doc.renewed_to && !ctx.draft_renewal
			&& ctx.is_accounts_user) {
			frm.add_custom_button(__("Raise Renewal Request"), () => raise_procurement(frm, ctx, "raise_renewal_request"), __("Create"));
		}
		if (ctx.draft_renewal) {
			frm.add_custom_button(__("Draft Renewal"), () => frappe.set_route("Form", "Insurance Policy", ctx.draft_renewal), __("View"));
		}

		// ── Endorsements ───────────────────────────────────────────────────
		if (live && ctx.is_accounts_manager) {
			const actions = (frm.doc.insurance_for || "Assets") === "Assets"
				? ["Add Assets", "Remove Assets", "Change Sum Insured", "Cancel Policy"]
				: ["Change Sum Insured", "Cancel Policy"];
			actions.forEach((action) => {
				frm.add_custom_button(__(action), () => endorse_dialog(frm, action), __("Endorse"));
			});
		}
		if (ctx.is_accounts_manager && (frm.doc.endorsements || []).some((e) => e.status === "Active")) {
			frm.add_custom_button(__("Reverse Endorsement"), () => reverse_dialog(frm), __("Endorse"));
		}

		// Deep link from Asset form / notifications.
		if (frappe.route_options && frappe.route_options.__endorse && live && ctx.is_accounts_manager) {
			const { __endorse, __asset } = frappe.route_options;
			frappe.route_options = null;
			endorse_dialog(frm, __endorse, __asset);
		}
	},
});

frappe.ui.form.on("Insurance Policy Asset", {
	asset(frm, cdt, cdn) {
		const row = locals[cdt][cdn];
		if (!row.asset) return;
		frappe.db.get_value("Asset", row.asset, ["value_after_depreciation", "net_purchase_amount"]).then((r) => {
			const v = r.message || {};
			frappe.model.set_value(cdt, cdn, "book_value_at_inception", flt(v.value_after_depreciation));
			if (!row.sum_insured) {
				frappe.model.set_value(cdt, cdn, "sum_insured", flt(v.value_after_depreciation) || flt(v.net_purchase_amount));
			}
		});
	},
	sum_insured: (frm) => set_sum_insured(frm),
	insured_assets_remove: (frm) => set_sum_insured(frm),
});

function set_address_display(frm, link_field, display_field) {
	if (!frm.doc[link_field]) {
		frm.set_value(display_field, "");
		return;
	}
	frappe.xcall("frappe.contacts.doctype.address.address.get_address_display", { address_dict: frm.doc[link_field] })
		.then((html) => frm.set_value(display_field, html || ""));
}

function set_total(frm) {
	frm.set_value("total_premium", flt(frm.doc.basic_premium) + flt(frm.doc.levies));
}

function set_sum_insured(frm) {
	const total = (frm.doc.insured_assets || [])
		.filter((r) => r.row_status !== "Removed")
		.reduce((s, r) => s + flt(r.sum_insured), 0);
	frm.set_value("total_sum_insured", total);
}

function raise_procurement(frm, ctx, method) {
	const run = (procurement) => {
		frappe.xcall(`${POLICY_API}.${method}`, { policy: frm.doc.name, procurement }).then((r) => {
			let msg = __("{0} {1} created.", [__(r.procurement_doctype), r.procurement_name]);
			if (r.policy && r.policy !== frm.doc.name) msg = __("Renewal policy {0} and ", [r.policy]) + msg;
			if (r.excluded && r.excluded.length) {
				msg += "<br>" + __("Excluded (scrapped / sold / no longer flagged): {0}", [r.excluded.join(", ")]);
			}
			frappe.msgprint({ message: msg, indicator: "green", title: __("Done") });
			frm.reload_doc();
			if (r.policy && r.policy !== frm.doc.name) frappe.set_route("Form", "Insurance Policy", r.policy);
		});
	};
	if (ctx.renewal_procurement !== "Ask") return run(ctx.renewal_procurement);
	frappe.prompt(
		{ fieldname: "procurement", fieldtype: "Select", label: __("Procure via"), options: "Material Request\nPurchase Order", reqd: 1, default: "Material Request" },
		(v) => run(v.procurement),
		__("Procurement"),
		__("Create")
	);
}

function endorse_dialog(frm, action, preset_asset) {
	const active = (frm.doc.insured_assets || []).filter((r) => r.row_status === "Active");
	const fields = [
		{ fieldname: "endorsement_no", fieldtype: "Data", label: __("Insurer's Endorsement No"), reqd: 1 },
		{ fieldname: "effective_date", fieldtype: "Date", label: __("Effective Date"), reqd: 1, default: frappe.datetime.get_today() },
		{ fieldtype: "Column Break" },
		{ fieldname: "remarks", fieldtype: "Small Text", label: __("Remarks") },
		{ fieldtype: "Section Break" },
	];
	if (action === "Add Assets") {
		fields.push({
			fieldname: "assets", fieldtype: "Table", label: __("Assets"), in_place_edit: true, reqd: 1,
			fields: [
				{ fieldname: "asset", fieldtype: "Link", options: "Asset", label: __("Asset"), in_list_view: 1, reqd: 1,
					get_query: () => ({ query: `${POLICY_API}.get_insurable_assets`, filters: { company: frm.doc.company, exclude: active.map((r) => r.asset) } }) },
				{ fieldname: "sum_insured", fieldtype: "Currency", label: __("Sum Insured"), in_list_view: 1, reqd: 1 },
			],
			data: preset_asset ? [{ asset: preset_asset }] : [],
		});
	} else if (action === "Remove Assets") {
		fields.push({
			fieldname: "assets", fieldtype: "Table", label: __("Assets to remove"), cannot_add_rows: true, in_place_edit: true,
			fields: [
				{ fieldname: "select", fieldtype: "Check", label: __("Remove"), in_list_view: 1, columns: 1 },
				{ fieldname: "asset", fieldtype: "Link", options: "Asset", label: __("Asset"), in_list_view: 1, read_only: 1 },
				{ fieldname: "sum_insured", fieldtype: "Currency", label: __("Sum Insured"), in_list_view: 1, read_only: 1 },
			],
			data: active.map((r) => ({ select: r.asset === preset_asset ? 1 : 0, asset: r.asset, sum_insured: r.sum_insured })),
		});
	} else if (action === "Change Sum Insured" && (frm.doc.insurance_for || "Assets") !== "Assets") {
		fields.push(
			{ fieldname: "current_sum_insured", fieldtype: "Currency", label: __("Current Sum Insured"), read_only: 1, default: frm.doc.total_sum_insured },
			{ fieldtype: "Column Break" },
			{ fieldname: "new_sum_insured", fieldtype: "Currency", label: __("New Sum Insured"), reqd: 1, default: frm.doc.total_sum_insured },
		);
	} else if (action === "Change Sum Insured") {
		fields.push({
			fieldname: "assets", fieldtype: "Table", label: __("Assets"), cannot_add_rows: true, in_place_edit: true,
			fields: [
				{ fieldname: "asset", fieldtype: "Link", options: "Asset", label: __("Asset"), in_list_view: 1, read_only: 1 },
				{ fieldname: "sum_insured", fieldtype: "Currency", label: __("Current"), in_list_view: 1, read_only: 1 },
				{ fieldname: "new_sum_insured", fieldtype: "Currency", label: __("New Sum Insured"), in_list_view: 1 },
			],
			data: active.map((r) => ({ asset: r.asset, sum_insured: r.sum_insured, new_sum_insured: r.sum_insured })),
		});
	} else {
		fields.push({ fieldtype: "HTML", options: `<p class="text-muted">${__("All {0} active asset row(s) will be removed and the policy marked Cancelled. The unexpired share of the total premium is returned via a debit note.", [active.length])}</p>` });
	}
	fields.push(
		{ fieldtype: "Section Break", label: __("Premium") },
		{ fieldname: "computed", fieldtype: "Currency", label: __("Pro-rata Premium Adjustment"), read_only: 1 },
		{ fieldname: "basis", fieldtype: "HTML" },
		{ fieldtype: "Column Break" },
		{ fieldname: "premium_adjustment", fieldtype: "Currency", label: __("Override Adjustment"),
			description: __("Leave blank to use the pro-rata amount. Negative = return premium.") },
		{ fieldname: "create_premium_document", fieldtype: "Check", label: __("Create premium document (draft)"), default: 1 },
	);

	const d = new frappe.ui.Dialog({
		title: __("Endorse: {0}", [__(action)]),
		size: "large",
		fields,
		primary_action_label: __("Endorse"),
		primary_action(values) {
			const payload = build_payload(action, values);
			frappe.xcall(`${POLICY_API}.endorse`, { policy: frm.doc.name, action, payload }).then((r) => {
				d.hide();
				frm.reload_doc();
				if (r.reference_name) {
					frappe.msgprint({
						message: __("Endorsement recorded. Draft {0} {1} created.", [__(r.reference_doctype),
							`<a href="/app/${frappe.router.slug(r.reference_doctype)}/${r.reference_name}">${r.reference_name}</a>`]),
						indicator: "green",
					});
				}
			});
		},
	});
	d.add_custom_action(__("Calculate"), () => preview(frm, d, action));
	["effective_date", "new_sum_insured"].forEach((f) => {
		if (d.fields_dict[f]) d.fields_dict[f].df.onchange = () => preview(frm, d, action);
	});
	d.show();
	if (action === "Cancel Policy" || preset_asset) preview(frm, d, action);
}

function build_payload(action, values) {
	let assets = values.assets || [];
	if (action === "Remove Assets") assets = assets.filter((r) => r.select);
	return {
		endorsement_no: values.endorsement_no,
		effective_date: values.effective_date,
		remarks: values.remarks,
		assets,
		new_sum_insured: values.new_sum_insured,
		premium_adjustment: values.premium_adjustment === undefined || values.premium_adjustment === null || values.premium_adjustment === "" ? null : values.premium_adjustment,
		create_premium_document: values.create_premium_document ? 1 : 0,
	};
}

function preview(frm, d, action) {
	const payload = build_payload(action, d.get_values(true) || {});
	const policy_level = (frm.doc.insurance_for || "Assets") !== "Assets";
	if (action !== "Cancel Policy" && !policy_level && !(payload.assets || []).some((a) => a.asset)) return;
	frappe.xcall(`${POLICY_API}.get_endorsement_preview`, { policy: frm.doc.name, action, payload }).then((p) => {
		d.set_value("computed", p.total);
		d.fields_dict.basis.$wrapper.html(
			`<div class="text-muted small">${__("{0} of {1} policy days remaining from the effective date.", [p.remaining_days, p.policy_days])}</div>`
		);
	});
}

function reverse_dialog(frm) {
	const groups = {};
	(frm.doc.endorsements || []).filter((e) => e.status === "Active").forEach((e) => {
		const g = (groups[e.endorsement_group] = groups[e.endorsement_group] || { ...e, assets: [], total: 0 });
		g.assets.push(e.asset);
		g.total += flt(e.premium_adjustment);
	});
	const options = Object.values(groups).map((g) => ({
		value: g.endorsement_group,
		label: `${g.endorsement_no} – ${__(g.endorsement_type)} – ${g.assets.join(", ")} (${format_currency(g.total, erpnext.get_currency(frm.doc.company))})${g.reference_name ? " · " + g.reference_name : ""}`,
	}));
	frappe.prompt(
		{ fieldname: "group", fieldtype: "Select", label: __("Endorsement"), options, reqd: 1,
			description: __("Allowed only while the premium document is draft or cancelled. A draft premium document is deleted.") },
		(v) => frappe.xcall(`${POLICY_API}.reverse_endorsement`, { policy: frm.doc.name, endorsement_group: v.group })
			.then(() => { frappe.show_alert({ message: __("Endorsement reversed"), indicator: "green" }); frm.reload_doc(); }),
		__("Reverse Endorsement"),
		__("Reverse")
	);
}
