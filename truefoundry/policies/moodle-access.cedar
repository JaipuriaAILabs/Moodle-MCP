// Attach this Cedar guardrail to jaipuria-moodle-prod on MCP Tool Pre-Invoke.
// TrueFoundry's Cedar engine is default-deny: every permitted surface is explicit.
// The Moodle MCP remains the authoritative row-level boundary for student ownership
// and dean campus scope; this policy only narrows which tools reach that boundary.

// Virtual MCP tools are renamed with a tenant-specific suffix. The virtual server's
// checked-in tool allowlist is therefore the tool boundary at this layer; the source
// policy below remains an independent backstop when TrueFoundry evaluates the
// underlying remote-server invocation with the original tool name.
permit (
    principal,
    action == Action::"execute_tool",
    resource == MCPServer::"jaipuria-moodle-student"
) when {
    principal is User &&
    principal.teamNames.contains("everyone")
};

permit (
    principal,
    action == Action::"execute_tool",
    resource == MCPServer::"jaipuria-moodle-staff"
) when {
    principal is User &&
    [
        "shreevats@jaipuria.ac.in",
        "shiva.kakkar@jaipuria.ac.in",
        "prabhat.pankaj@jaipuria.ac.in",
        "shailaja.sharma@jaipuria.ac.in",
        "amit.attry@jaipuria.ac.in",
        "sauresh.mehrotra@jaipuria.ac.in",
        "kanchan.rana@jaipuria.ac.in",
        "sandip.das@jaipuria.ac.in",
        "rahul.singh@jaipuria.ac.in",
        "sushma.vishnani@jaipuria.ac.in",
        "daneshwar.sharma@jaipuria.ac.in",
        "deepankar.chakrabarti@jaipuria.ac.in"
    ].contains(principal.email)
};

permit (
    principal,
    action == Action::"execute_tool",
    resource == MCPServer::"jaipuria-moodle-prod"
) when {
    principal is User &&
    principal.teamNames.contains("everyone") &&
    [
        "whoami",
        "get_student",
        "student_marks",
        "student_attendance",
        "student_trajectory",
        "student_360",
        "get_student_report",
        "create_report",
        "get_report_job",
        "resolve_identities"
    ].contains(context.tool_name)
};

permit (
    principal,
    action == Action::"execute_tool",
    resource == MCPServer::"jaipuria-moodle-prod"
) when {
    principal is User &&
    [
        "shreevats@jaipuria.ac.in",
        "shiva.kakkar@jaipuria.ac.in",
        "prabhat.pankaj@jaipuria.ac.in",
        "shailaja.sharma@jaipuria.ac.in",
        "amit.attry@jaipuria.ac.in",
        "sauresh.mehrotra@jaipuria.ac.in",
        "kanchan.rana@jaipuria.ac.in",
        "sandip.das@jaipuria.ac.in",
        "rahul.singh@jaipuria.ac.in",
        "sushma.vishnani@jaipuria.ac.in",
        "daneshwar.sharma@jaipuria.ac.in",
        "deepankar.chakrabarti@jaipuria.ac.in"
    ].contains(principal.email) &&
    [
        "whoami",
        "list_students",
        "get_student",
        "student_marks",
        "student_attendance",
        "list_subjects",
        "subject_performance",
        "section_compare",
        "assessment_breakdown",
        "subject_difficulty",
        "campus_performance_report",
        "declining_students",
        "student_trajectory",
        "student_360",
        "cohort_pulse",
        "watchlist",
        "marks_overview",
        "attendance_overview",
        "top_performers",
        "cohort_compare",
        "at_risk_students",
        "attendance_watch",
        "zero_alerts",
        "get_student_report",
        "report_data_availability",
        "create_report",
        "get_report_job",
        "resolve_identities"
    ].contains(context.tool_name)
};
