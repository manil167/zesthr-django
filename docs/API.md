# API v1

The current API is read-only. It supplies a foundation for integrations; it is not a complete native-mobile backend.

| Endpoint | Access |
| --- | --- |
| /api/v1/employees/ | Employee-directory permission; active employees in authorized organizations |
| /api/v1/attendance/ | Company attendance-view permission and tenant scope |
| /api/v1/leaves/ | Own employee requests, or authorized organization-wide leave access |
| /api/v1/payroll/ | Own employee payslips, or authorized all-payslip access within tenant scope |

Each collection supports a detail route using its primary key. Out-of-scope objects are excluded before retrieval. Lists use page-number pagination with 50 records per page; follow the returned next/previous links. The JSON envelope contains count, next, previous, and results.

Authentication uses Django sessions. There is no supported token-issuing endpoint. Do not embed staff passwords or session cookies in a distributed mobile application. GET/HEAD/OPTIONS are supported by the read-only viewsets; mutations must use the protected web workflows. Future API write operations must call the same business services, enforce authorization and idempotency, and preserve auditing.

Anonymous requests are denied. Authentication alone grants no directory or organization-wide attendance access. Users without a linked employee and without broader permissions receive empty leave/payroll collections. Salary and attendance serialization are explicit; see core/api/serializers.py for the current field contract.

Before exposing mobile integrations, design revocable authentication, device/session handling, rate limits, API schema/version compatibility, and security monitoring. The web application's permission and business-rule tests remain required for every added operation.
