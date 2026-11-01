"""Deterministic app guidance: no employee data or external AI requests."""
TOPICS = [
    (('correction', 'correct a', 'missed punch', 'missing punch'),
     'Open My corrections, choose New correction, select the date and explain what needs correcting. Track the request and review outcome there. The authorized reviewer applies approved changes with an audit record.', '/attendance/corrections/mine/'),
    (('punch', 'clock', 'attendance', 'check in', 'check out'),
     'Open Punch Attendance to check in or check out. Your local shift time is recorded. For a missed punch, open My corrections, choose the date and explain the correction. HR punches still use the same attendance rules.', '/attendance/today/'),
    (('leave', 'holiday', 'vacation'),
     'Open Leaves → Apply leave, select the type and dates, and submit a reason. Track decisions in My Leave Requests and your notifications.', '/leaves/'),
    (('pay', 'salary', 'payslip'),
     'Open your employee portal to view available payslips. Payroll operators calculate and review runs before independent approval and finalization. Contact HR if a payslip is missing.', '/me/'),
    (('whatsapp', 'bot', 'phone'),
     'Open WhatsApp connection, generate a temporary code, and send LINK followed by that code to your organization’s configured bot number. Then use IN, OUT, STATUS or NOTIFICATIONS. Never share the code.', '/whatsapp/'),
    (('announce', 'broadcast', 'notification', 'message'),
     'Notifications shows your updates and organization announcements. Authorized HR can use Send announcement and select an organization they belong to. Every active member of that organization receives it in the app.', '/notifications/'),
    (('organization', 'company', 'tenant', 'department', 'branch'),
     'Organizations own companies and reference data. Branches, divisions, grades and cost centers belong to a company; teams belong to divisions. Platform administrators assign memberships. You cannot join an organization simply by selecting its name.', '/profile/'),
    (('login', 'sign in', 'sign up', 'password', 'register'),
     'Create an account from Sign up, or use the invitation HR provided. Public signup does not grant organization access. Use Forgot password to reset your password and ask HR to link your employee profile.', '/password/reset/'),
]


def answer(question):
    question = question.lower().strip()
    for keywords, text, url in TOPICS:
        if any(word in question for word in keywords):
            return {'answer': text, 'url': url}
    return {'answer': 'I can explain attendance, leave, payslips, notifications, WhatsApp and account access. What would you like help with? I provide app guidance; ask your HR team about organization-specific policy.', 'url': ''}
