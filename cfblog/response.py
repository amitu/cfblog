__author__ = 'vinay'
import json

from django.conf import settings
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.http.response import Http404, HttpResponse
from django.template import TemplateSyntaxError, TemplateDoesNotExist
from django.views.decorators.csrf import csrf_protect

from .models import Content
from .utils import can_edit_content, parse_cms_template, NAMESPACE_DELIMITER, dum_request
from .validators import validate_and_get_template


_template_not_defined = object()


def render(request, template_name=_template_not_defined,
           template_context=None, content_type=None, status=None,
           using=None, cms_context=None):
    """
    If request is given:
    Checks if an entry exists in the Content model for the request path and renders it.
    If it fails with 404 and a template_name is given,
    then it falls back to render_to_response with the given arguments.

    If request is not passed:
    render_to_response is called with the given arguments.

    If render_to_response fails with ValueError or Http404, then it will be rendered without cms_context.
    """
    if request is not None:
        try:
            cms_page = Content.objects.get(url=request.path_info)
        except Content.DoesNotExist:
            pass
        else:
            try:
                return render_content(cms_page,
                                      request=request, template_context=template_context,
                                      content_type=content_type, status=status, using=using)
            except (TemplateDoesNotExist, Http404):
                if settings.DEBUG:
                    raise

    if template_name is _template_not_defined:
        raise Http404('Backup template name is not defined')

    try:
        return render_to_response(template_name, template_context, content_type, status, using, request, cms_context)
    except (ValueError, Http404):
        if settings.DEBUG:
            raise
        else:
            return render_to_response(template_name, template_context, content_type, status, using, request)


def render_to_response(template_name,
                       template_context=None, content_type=None, status=None,
                       using=None, request=None, cms_context=None):

    """
    This is similar to the django's render_to_response and accepts all of it's non deprecated arguments.
    It also takes two additional parameters request and cms_context.

    It will render the given template with given template_context and cms_context and returns corresponding response
    or raises Http404 if the parsing fails for the given cms_context.

    :rtype: HttpResponse

    :return: :raise ValueError:
    """
    @csrf_protect
    def _render(_request):
        template = validate_and_get_template(template_name, using=using)
        content = template.render(context=template_context, request=_request)

        if cms_context is not None:
            if isinstance(cms_context, dict):
                try:
                    content = parse_cms_template(content, cms_context,
                                                 publish=False, request=_request)
                except (ValidationError, TemplateSyntaxError) as e:
                    raise Http404(e)
            else:
                raise ValueError('cms_context should be an instance of dict')

        return HttpResponse(content, content_type, status)

    # if you wish to know why the view or other render functions are not decorated with csrf_protect
    # read the comment in django.contrib.flatpages.views
    if request is None:
        # since we are using csrf_token decorator
        # it expects the first argument of the function to be a request object
        # and we use this dum_request to render the template instead of passing None
        # so as to load the csrf_token inside the template
        request = dum_request
    return _render(request)


def render_content(cms_page,
                   request=None, template_context=None,
                   content_type=None, status=None, using=None):
    """
    Renders a given Content object and returns the HttpResponse
    or will raise Http404 if any syntax or validation errors occur.

    The response object is processed with auth_data or public_data according to the given request.

    Since we are passing an instance of Content object, this function might raise TemplateDoesNotExist
    for the template associated with the Content instance.

    :type cms_page: Content

    :type request: HttpRequest | None

    :type template_context: dict | None

    :rtype: HttpResponse

    :return: :raise Http404:
    """
    template_context = template_context or {}
    template_context['cms_content'] = cms_page

    if request is not None and can_edit_content(request.user):
        editor_context = {'view': 'author',
                          'cms_data': json.dumps(cms_page.auth_data),
                          'cms_page_id': cms_page.id,
                          'namespace_delimiter': json.dumps(NAMESPACE_DELIMITER)}

        cache.set('template_context_{}'.format(cms_page.id), template_context, 24 * 60 * 60)
        template_context.update(editor_context)

        return render_to_response(cms_page.template, template_context,
                                  content_type, status, using, request, cms_page.auth_data)

    if cms_page.is_public:
        return render_to_response(cms_page.template, template_context,
                                  content_type, status, using, request, cms_page.public_data)

    raise Http404('Page not found')
