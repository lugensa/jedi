import jedi
import pytest
from os.path import dirname, join


def script_with_path(*args, **kwargs):
    sys_path = [join(dirname(__file__), 'namespace_package/%s' % d)
                for d in ['ns1', 'ns2', 'ns3', 'ns4' ]]
    return jedi.Script(sys_path=sys_path, *args, **kwargs)


def test_namespace_packages_do_not_loose_their_parent_on_caching():
    assert script_with_path(
        'from pkg.subpkg import ns3_folder').goto_definitions()
    assert script_with_path(
        'from pkg.subpkg import ns4_folder').goto_definitions()


def test_namespace_packages_have_valid_goto_definitions():
    # goto definition
    assert script_with_path('from pkg import ns1_file').goto_definitions()
    assert script_with_path('from pkg import ns2_file').goto_definitions()
    assert not script_with_path('from pkg import ns3_file').goto_definitions()


def test_namespace_packages_have_valid_goto_assignments():
    # goto assignment
    tests = {
        'from pkg.ns2_folder.nested import foo': 'nested!',
        'from pkg.ns2_folder import foo': 'ns2_folder!',
        'from pkg.ns2_file import foo': 'ns2_file!',
        'from pkg.ns1_folder import foo': 'ns1_folder!',
        'from pkg.ns1_file import foo': 'ns1_file!',
        'from pkg import foo': 'ns1!',
    }
    for source, solution in tests.items():
        ass = script_with_path(source).goto_assignments()
        assert len(ass) == 1
        assert ass[0].description == "foo = '%s'" % solution


def test_namespace_packages_have_valid_completions():
    # completion
    completions = script_with_path('from pkg import ').completions()
    names = [str(c.name) for c in completions]  # str because of unicode
    compare = ['foo', 'ns1_file', 'ns1_folder', 'ns2_folder', 'ns2_file',
               'pkg_resources', 'pkgutil', '__name__', '__path__',
               '__package__', '__file__', '__doc__', 'subpkg']
    # must at least contain these items, other items are not important
    assert set(compare) == set(names)

    tests = {
        'from pkg import ns2_folder as x': 'ns2_folder!',
        'from pkg import ns2_file as x': 'ns2_file!',
        'from pkg.ns2_folder import nested as x': 'nested!',
        'from pkg import ns1_folder as x': 'ns1_folder!',
        'from pkg import ns1_file as x': 'ns1_file!',
        'import pkg as x': 'ns1!',
    }
    for source, solution in tests.items():
        for c in script_with_path(source + '; x.').completions():
            if c.name == 'foo':
                completion = c
        solution = "statement: foo = '%s'" % solution
        assert completion.description == solution


def test_namespace_pkgs_have_valid_completions_on_absolute_module_path():
    completions = script_with_path("import pkg; pkg.").completions()
    names = [str(c.name) for c in completions]
    compare = ['foo', 'ns1_file', 'ns1_folder', 'ns2_folder', 'ns2_file',
               'pkg_resources', 'pkgutil', '__name__', '__path__',
               '__package__', '__file__', '__doc__', 'subpkg']
    assert set(compare) == set(names)


def test_namespace_pkgs_have_valid_completions_on_absolute_module_path():
    completions = script_with_path(
        "import pkg.subpkg; pkg.subpkg.").completions()
    names = [str(c.name) for c in completions]
    print names
    compare = ['foo', 'ns4_folder',  'ns3_folder',
               'pkg_resources', 'pkgutil', '__name__', '__path__',
               '__package__', '__file__', '__doc__']
    assert set(compare) == set(names)


@pytest.mark.xfail
def test_namespace_package_completion_is_restricted_to_imported_names():
    # (mg): The completion should only give subpkg because its imported
    # Instead we get all possible completions for pkg
    # This is acceptable but in my opinion not strong enough.
    completions = script_with_path("import pkg.subpkg; pkg.").completions()
    names = [str(c.name) for c in completions]
    assert set(['subpkg']) == set(names)


def test_nested_namespace_package():
    CODE = 'from nested_namespaces.namespace.pkg import CONST'

    sys_path = [dirname(__file__)]

    script = jedi.Script(sys_path=sys_path, source=CODE, line=1, column=45)

    result = script.goto_definitions()

    assert len(result) == 1
