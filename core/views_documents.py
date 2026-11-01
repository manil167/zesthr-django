from django import forms
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.http import FileResponse, Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from apps.organizations.tenancy import scope_queryset
from .models import Employee, EmployeeDocument, DocumentEvent
from .services import documents


class UploadDocumentForm(forms.Form):
    title = forms.CharField(max_length=150)
    document_type = forms.ChoiceField(choices=EmployeeDocument.TYPES)
    upload = forms.FileField(help_text="Unencrypted PDF, maximum 10 MB and 500 pages.")
    employee_visible = forms.BooleanField(required=False, label="Allow this employee to download the document")
    retain_until = forms.DateField(required=False, widget=forms.DateInput(attrs={"type": "date"}))


@login_required
def document_list(request, pk):
    employee = get_object_or_404(scope_queryset(Employee.objects.all(), request.user), pk=pk)
    can_edit = documents.authorize_employee(employee, request.user, edit=request.method == "POST")
    form = UploadDocumentForm(request.POST or None, request.FILES or None)
    if request.method == "POST" and form.is_valid():
        try:
            documents.upload_document(employee, by=request.user, **form.cleaned_data)
        except ValidationError as exc:
            form.add_error(None, exc)
        else:
            messages.success(request, "Employee document uploaded.")
            return redirect("employee_documents", pk=pk)
    items = documents.visible_documents(employee, request.user)
    return render(request, "employee_documents.html", {"employee": employee, "form": form,
        "documents": Paginator(items, 30).get_page(request.GET.get("page")),
        "can_upload": can_edit and request.user.has_perm("core.change_employee") and settings.DOCUMENT_UPLOADS_ENABLED})


@login_required
def download(request, pk):
    queryset = scope_queryset(EmployeeDocument.objects.select_related("employee"),
                              request.user, "employee__organization")
    if not request.user.has_perm("core.view_employee_personal"):
        queryset = queryset.filter(employee__user=request.user, employee_visible=True)
    document = get_object_or_404(queryset, pk=pk)
    documents.authorize_employee(document.employee, request.user)
    if not documents.visible_documents(document.employee, request.user).filter(pk=pk).exists():
        raise Http404
    try:
        handle = document.file.open("rb")
    except FileNotFoundError:
        raise Http404("This document is unavailable.") from None
    DocumentEvent.objects.create(document=document, action="DOWNLOADED", actor=request.user)
    response = FileResponse(handle, as_attachment=True, filename=document.original_name, content_type="application/pdf")
    response["Cache-Control"] = "private, no-store"
    response["X-Content-Type-Options"] = "nosniff"
    return response


@login_required
@require_POST
def request_purge(request, pk):
    queryset = scope_queryset(EmployeeDocument.objects.select_related("employee"),
                              request.user, "employee__organization")
    if not request.user.has_perm("core.view_employee_personal"):
        queryset = queryset.filter(employee__user=request.user, employee_visible=True)
    document = get_object_or_404(queryset, pk=pk)
    try:
        documents.request_purge(document, by=request.user, reason=request.POST.get("reason", ""))
    except ValidationError as exc:
        messages.error(request, "; ".join(exc.messages))
    else:
        messages.success(request, "Deletion requested. The retention worker will process it.")
    return redirect("employee_documents", pk=document.employee_id)
