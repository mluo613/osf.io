from django.conf.urls import url
from api.nodes import views

urlpatterns = [
    # Examples:
    # url(r'^$', 'api.views.home', name='home'),
    # url(r'^blog/', include('blog.urls')),
    url(r'^$', views.NodeList.as_view(), name='node-list'),
    url(r'^(?P<pk>\w+)/$', views.NodeDetail.as_view(), name='node-detail'),
    url(r'^(?P<pk>\w+)/contributors/$', views.NodeContributorsList.as_view(), name='node-contributors'),
]
