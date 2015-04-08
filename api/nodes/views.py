from rest_framework import generics, permissions as drf_permissions
from modularodm import Q

from website.models import Node
from api.base.utils import get_object_or_404
from .serializers import NodeSerializer
from api.users.serializers import UserSerializer
from .permissions import ContributorOrPublic, ReadOnlyIfRegistration

class NodeList(generics.ListCreateAPIView):
    """Return a list of nodes. By default, a GET
    will return a list of nodes the current user contributes
    to.
    """
    # TODO: Allow unauthenticated requests (list public projects, e.g.)
    permission_classes = (
        drf_permissions.IsAuthenticated,
    )
    serializer_class = NodeSerializer

    # override
    def get_queryset(self):
        user = self.request.user
        # Return list of nodes that current user contributes to
        return Node.find(Q('contributors', 'eq', user._id))

    # override
    def perform_create(self, serializer):
        # On creation, make sure that current user is the creator
        user = self.request.user
        serializer.save(creator=user)


class NodeDetail(generics.RetrieveUpdateAPIView):

    permission_classes = (
        ContributorOrPublic,
        ReadOnlyIfRegistration,
    )
    serializer_class = NodeSerializer

    # override
    # TODO: Generalize this.
    def get_object(self):
        lookup_url_kwarg = self.lookup_url_kwarg or self.lookup_field
        obj = get_object_or_404(Node, self.kwargs[lookup_url_kwarg])
        # May raise a permission denied
        self.check_object_permissions(self.request, obj)
        return obj

    # override
    def get_serializer_context(self):
        # Serializer needs the request in order to make an update to privacy
        return {'request': self.request}

class NodeContributorsList(generics.ListAPIView):
    """Return the contributors (users) fora node."""

    permissions_classes = (
        ContributorOrPublic,
    )

    serializer_class = UserSerializer

    def get_queryset(self):
        # TODO: Duplication here. Rethink.
        node = get_object_or_404(Node, self.kwargs['pk'])
        # May raise a permission denied
        self.check_object_permissions(self.request, node)
        return node.visible_contributors
