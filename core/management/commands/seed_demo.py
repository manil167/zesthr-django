import random
from datetime import date, time, timedelta

from django.conf import settings
from django.contrib.auth.models import Group, User
from django.core.management.base import BaseCommand, CommandError

from core import roles
from core.models import (Attendance, Department, Employee, EmploymentType, Holiday, JobPosition,
                         LeaveApplication, WorkLocation, WorkSchedule)
from core.roles import sync_roles


class Command(BaseCommand):
    help = "Seed admin user, 10 dummy employees with logins (emp123), Indian Govt holidays, leaves, and attendance."

    def handle(self, *args, **opts):
        if not settings.DEBUG:
            raise CommandError("seed_demo creates accounts with well-known passwords; it only runs when DEBUG is True.")
        sync_roles()

        # 1. Admin Superuser
        admin_user, _ = User.objects.get_or_create(
            username="admin",
            defaults={"email": "admin@hros.local", "is_staff": True, "is_superuser": True}
        )
        admin_user.set_password("admin123")
        admin_user.save()
        admin_user.groups.add(Group.objects.get(name=roles.HR_ADMIN))
        self.stdout.write(self.style.SUCCESS("[OK] Admin user created / updated: admin / admin123"))

        # Demo accounts for the other staff roles (no employee profile).
        for uname, role in (("hr.officer", roles.HR_OFFICER), ("payroll.officer", roles.PAYROLL_OFFICER)):
            su, _ = User.objects.get_or_create(username=uname, defaults={"email": f"{uname}@hros.local"})
            su.set_password("staff123")
            su.is_staff = su.is_superuser = False
            su.save()
            su.groups.set([Group.objects.get(name=role)])
            self.stdout.write(self.style.SUCCESS(f"[OK] {role} demo account: {uname} / staff123"))

        # 2. 10 Dummy Employees with User Logins
        employees_data = [
            ("Aarav Sharma", "aarav.sharma", "Senior Software Engineer", 65000, "9876543210"),
            ("Priya Patel", "priya.patel", "UI/UX Designer", 48000, "9876543211"),
            ("Rohit Verma", "rohit.verma", "Backend Developer", 52000, "9876543212"),
            ("Ananya Iyer", "ananya.iyer", "HR Specialist", 42000, "9876543213"),
            ("Vikram Singh", "vikram.singh", "DevOps Engineer", 58000, "9876543214"),
            ("Neha Gupta", "neha.gupta", "QA Automation Engineer", 40000, "9876543215"),
            ("Karan Mehta", "karan.mehta", "Financial Analyst", 45000, "9876543216"),
            ("Sneha Reddy", "sneha.reddy", "Growth Marketer", 50000, "9876543217"),
            ("Arjun Nair", "arjun.nair", "Product Manager", 70000, "9876543218"),
            ("Pooja Joshi", "pooja.joshi", "Operations Associate", 35000, "9876543219"),
        ]

        emp_objs = []
        for name, uname, desig, sal, ph in employees_data:
            # Create user account
            first, last = name.split(" ", 1)
            u, _ = User.objects.get_or_create(
                username=uname,
                defaults={"email": f"{uname}@hros.local", "first_name": first, "last_name": last}
            )
            u.set_password("emp123")
            u.is_staff = False
            u.is_superuser = False
            u.save()
            u.groups.set([Group.objects.get(name=roles.EMPLOYEE)])

            # Create or update Employee profile linked to user
            emp, _ = Employee.objects.get_or_create(
                name=name,
                defaults={
                    "user": u,
                    "designation": desig,
                    "basic_salary": sal,
                    "phone": ph,
                    "is_active": True,
                }
            )
            if emp.user != u:
                emp.user = u
                emp.designation = desig
                emp.basic_salary = sal
                emp.phone = ph
                emp.save()
            emp_objs.append(emp)

        self.stdout.write(self.style.SUCCESS("[OK] 10 Employees and logins created (password: emp123)"))

        # 3. Indian Government Holidays (2026 Official Calendar)
        holidays_data = [
            # Gazetted Holidays (Mandatory Public Holidays)
            ("Republic Day", date(2026, 1, 26), "Monday", "gazetted", "Honours the Constitution of India coming into effect."),
            ("Maha Shivratri", date(2026, 2, 15), "Sunday", "gazetted", "Celebrated nationwide in honour of Lord Shiva."),
            ("Holi", date(2026, 3, 4), "Wednesday", "gazetted", "Spring festival of colours and joy."),
            ("Id-ul-Fitr", date(2026, 3, 21), "Saturday", "gazetted", "Islamic festival marking the culmination of Ramadan fasting."),
            ("Mahavir Jayanti", date(2026, 3, 31), "Tuesday", "gazetted", "Birth anniversary of Bhagwan Mahavira, 24th Tirthankara."),
            ("Good Friday", date(2026, 4, 3), "Friday", "gazetted", "Christian holy day commemorating the crucifixion of Jesus Christ."),
            ("Buddha Purnima", date(2026, 5, 1), "Friday", "gazetted", "Celebration of Gautama Buddha's birth and enlightenment."),
            ("Id-ul-Zuha (Bakrid)", date(2026, 5, 27), "Wednesday", "gazetted", "Feast of Sacrifice observed with solemnity and prayers."),
            ("Muharram", date(2026, 6, 26), "Friday", "gazetted", "Day of Ashura commemorating the martyrdom of Imam Hussain."),
            ("Independence Day", date(2026, 8, 15), "Saturday", "gazetted", "Celebration of India's freedom from British colonial rule."),
            ("Milad-un-Nabi (Id-e-Milad)", date(2026, 8, 26), "Wednesday", "gazetted", "Observance of the birth anniversary of Prophet Muhammad."),
            ("Mahatma Gandhi Jayanti", date(2026, 10, 2), "Friday", "gazetted", "National tribute honoring Father of the Nation, Mahatma Gandhi."),
            ("Dussehra (Vijaya Dashami)", date(2026, 10, 20), "Tuesday", "gazetted", "Celebration of the victory of good over evil."),
            ("Diwali (Deepavali)", date(2026, 11, 8), "Sunday", "gazetted", "Grand national festival of lights, prosperity, and harmony."),
            ("Guru Nanak Jayanti", date(2026, 11, 24), "Tuesday", "gazetted", "Celebration of the birth of Guru Nanak Dev Ji."),
            ("Christmas Day", date(2026, 12, 25), "Friday", "gazetted", "Worldwide celebration of the birth of Jesus Christ."),

            # Restricted Holidays (Optional / Cultural Public Holidays)
            ("Guru Gobind Singh Jayanti", date(2026, 1, 5), "Monday", "restricted", "Celebration of the tenth Sikh Guru."),
            ("Makar Sankranti / Pongal", date(2026, 1, 14), "Wednesday", "restricted", "Traditional solar harvest festival."),
            ("Ram Navami", date(2026, 3, 27), "Friday", "restricted", "Birth celebrations of Lord Rama."),
            ("Raksha Bandhan", date(2026, 8, 28), "Friday", "restricted", "Festival celebrating fraternal bonds and protection."),
            ("Janmashtami", date(2026, 9, 4), "Friday", "restricted", "Grand festival marking the birth of Lord Krishna."),
            ("Ganesh Chaturthi", date(2026, 9, 14), "Monday", "restricted", "Auspicious arrival of Lord Ganesha onto earth."),
            ("Govardhan Puja", date(2026, 11, 9), "Monday", "restricted", "Post-Diwali commemoration of Lord Krishna lifting Govardhan."),
            ("Bhai Duj", date(2026, 11, 10), "Tuesday", "restricted", "Auspicious occasion marking affection between brothers and sisters."),
        ]

        for name, h_date, day_name, h_type, desc in holidays_data:
            Holiday.objects.update_or_create(
                name=name, date=h_date,
                defaults={"day": day_name, "holiday_type": h_type, "description": desc}
            )
        self.stdout.write(self.style.SUCCESS(f"[OK] {len(holidays_data)} Indian Govt Holidays populated."))

        # 4. Generate Attendance for Current Month
        today = date.today()
        day = today.replace(day=1)
        while day <= today:
            if day.weekday() < 6:  # Mon-Sat
                for e in emp_objs:
                    # Keep today unmarked or present for demo
                    if day == today:
                        if e == emp_objs[0]:  # Aarav Sharma: Punched IN today
                            Attendance.objects.update_or_create(
                                emp=e, date=day,
                                defaults={"status": "P", "check_in": time(9, 15), "check_out": None}
                            )
                        elif e == emp_objs[1]:  # Priya Patel: Completed day
                            Attendance.objects.update_or_create(
                                emp=e, date=day,
                                defaults={"status": "P", "check_in": time(9, 5), "check_out": time(18, 10)}
                            )
                        continue

                    st = random.choices(["P", "P", "P", "H", "A"], weights=[80, 10, 5, 3, 2])[0]
                    ci, co = (time(9, random.randint(0, 30)), time(17, random.randint(30, 59))) if st in ("P", "H") else (None, None)
                    Attendance.objects.get_or_create(emp=e, date=day, defaults={"status": st, "check_in": ci, "check_out": co})
            day += timedelta(days=1)
        self.stdout.write(self.style.SUCCESS("[OK] Current month attendance logs generated."))

        # 5. Sample Leave Applications (Demonstrating 1 Sick Leave / Mo Policy & Approval Flow)
        # Neha Gupta: used her 1 Sick Leave for this month (Approved)
        LeaveApplication.objects.get_or_create(
            emp=emp_objs[5],  # Neha Gupta
            leave_type="SL",
            start_date=today.replace(day=max(1, min(today.day - 2, 28))),
            end_date=today.replace(day=max(1, min(today.day - 2, 28))),
            defaults={
                "reason": "Severe viral fever and flu symptoms.",
                "status": "APPROVED",
                "admin_note": "Approved by HR. Get well soon.",
            }
        )

        # Rohit Verma: Pending Sick Leave application
        LeaveApplication.objects.get_or_create(
            emp=emp_objs[2],  # Rohit Verma
            leave_type="SL",
            start_date=today,
            end_date=today,
            defaults={
                "reason": "Sudden migraine and throat infection.",
                "status": "PENDING",
            }
        )

        # Arjun Nair: Pending Casual Leave application
        LeaveApplication.objects.get_or_create(
            emp=emp_objs[8],  # Arjun Nair
            leave_type="CL",
            start_date=today + timedelta(days=3),
            end_date=today + timedelta(days=4),
            defaults={
                "reason": "Family wedding function in hometown.",
                "status": "PENDING",
            }
        )

        self.stdout.write(self.style.SUCCESS("[OK] Sample leave requests (Pending & Approved) seeded."))

        # Organization data: departments, a location and a standard schedule; employees
        # are spread across the departments and the first employee manages the rest.
        depts = [Department.objects.get_or_create(code=c, defaults={"name": n})[0]
                 for c, n in (("ENG", "Engineering"), ("OPS", "Operations"), ("FIN", "Finance"))]
        office, _ = WorkLocation.objects.get_or_create(name="Head Office", defaults={"city": "Mumbai", "country": "India"})
        sched, _ = WorkSchedule.objects.get_or_create(
            name="Standard day", defaults={"start_time": time(9, 0), "end_time": time(18, 0), "break_minutes": 60,
                                           "grace_minutes": 10, "overtime_eligible": True, "is_default": True})
        full_time, created = EmploymentType.objects.get_or_create(name="Full-time")
        people = list(Employee.objects.filter(user__username__regex=r"^[a-z]+\.[a-z]+$")
                      .exclude(user__username__in=["hr.officer", "payroll.officer"]).order_by("pk"))
        lead = people[0] if people else None
        for i, e in enumerate(people):
            e.department = depts[i % len(depts)]
            e.position = JobPosition.objects.get_or_create(title=e.designation, defaults={"department": e.department})[0]
            e.employment_type, e.work_location, e.work_schedule = full_time, office, sched
            e.manager = lead if e != lead else None
            e.save()
        self.stdout.write(self.style.SUCCESS("[OK] Departments, location, schedule and reporting lines seeded."))
        self.stdout.write(self.style.SUCCESS("\n" + "="*50))
        self.stdout.write(self.style.SUCCESS("Demo setup complete!"))
        self.stdout.write(self.style.SUCCESS("HR Admin:   admin / admin123"))
        self.stdout.write(self.style.SUCCESS("Employees:  aarav.sharma / emp123 (and 9 others)"))
        self.stdout.write(self.style.SUCCESS("="*50))
