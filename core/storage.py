"""Private storage indirection: files have no public media URL."""
import uuid
from django.core.files.storage import Storage, storages
from django.utils.deconstruct import deconstructible


@deconstructible
class PrivateDocumentStorage(Storage):
    @property
    def backend(self):
        return storages["private_documents"]

    def _open(self, name, mode="rb"):
        return self.backend.open(name, mode)

    def _save(self, name, content):
        return self.backend.save(name, content)

    def exists(self, name):
        return self.backend.exists(name)

    def delete(self, name):
        return self.backend.delete(name)

    def size(self, name):
        return self.backend.size(name)

    def url(self, name):
        raise ValueError("Private documents are available only through authorized downloads.")


def document_path(instance, filename):
    return f"employees/{instance.employee.organization_id}/{instance.employee_id}/{uuid.uuid4().hex}.pdf"
