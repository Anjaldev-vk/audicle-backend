# utils/pagination.py

from rest_framework.pagination import PageNumberPagination
from utils.response import success_response


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100

    def get_paginated_response(self, data):
        return success_response(
            message="Data retrieved successfully.",
            data={
                'results': data,
            'pagination': {
                'total': self.page.paginator.count,
                'page': self.page.number,
                'page_size': self.get_page_size(self.request),
                'total_pages': self.page.paginator.num_pages,
                'next': self.get_next_link(),
                'previous': self.get_previous_link(),
            }
        })

    def get_paginated_response_schema(self, schema):
        return {
            'type': 'object',
            'properties': {
                'success': {'type': 'boolean'},
                'data': {
                    'type': 'object',
                    'properties': {
                        'results': schema,
                        'pagination': {
                            'type': 'object',
                            'properties': {
                                'total': {'type': 'integer'},
                                'page': {'type': 'integer'},
                                'page_size': {'type': 'integer'},
                                'total_pages': {'type': 'integer'},
                                'next': {'type': 'string', 'nullable': True},
                                'previous': {'type': 'string', 'nullable': True},
                            }
                        }
                    }
                }
            }
        }
