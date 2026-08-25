from rest_framework.pagination import CursorPagination


class DefaultCursorPagination(CursorPagination):
    ordering = "-created_at"
    page_size = 50
