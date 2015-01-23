"""
The ``Parser`` tries to convert the available Python code in an easy to read
format, something like an abstract syntax tree. The classes who represent this
tree, are sitting in the :mod:`jedi.parser.tree` module.

The Python module ``tokenize`` is a very important part in the ``Parser``,
because it splits the code into different words (tokens).  Sometimes it looks a
bit messy. Sorry for that! You might ask now: "Why didn't you use the ``ast``
module for this? Well, ``ast`` does a very good job understanding proper Python
code, but fails to work as soon as there's a single line of broken code.

There's one important optimization that needs to be known: Statements are not
being parsed completely. ``Statement`` is just a representation of the tokens
within the statement. This lowers memory usage and cpu time and reduces the
complexity of the ``Parser`` (there's another parser sitting inside
``Statement``, which produces ``Array`` and ``Call``).
"""
import os

from jedi.parser import tree as pt
from jedi.parser import tokenize
from jedi.parser import token
from jedi.parser.pgen2.pgen import generate_grammar
from jedi.parser.pgen2.parse import PgenParser

OPERATOR_KEYWORDS = 'and', 'for', 'if', 'else', 'in', 'is', 'lambda', 'not', 'or'
# Not used yet. In the future I intend to add something like KeywordStatement
STATEMENT_KEYWORDS = 'assert', 'del', 'global', 'nonlocal', 'raise', \
    'return', 'yield', 'pass', 'continue', 'break'


_loaded_grammars = {}


def load_grammar(file='grammar3.4'):
    # For now we only support two different Python syntax versions: The latest
    # Python 3 and Python 2. This may change.
    if file.startswith('grammar3'):
        file = 'grammar3.4'
    else:
        file = 'grammar2.7'

    global _loaded_grammars
    path = os.path.join(os.path.dirname(__file__), file) + '.txt'
    try:
        return _loaded_grammars[path]
    except KeyError:
        return _loaded_grammars.setdefault(path, generate_grammar(path))


class ErrorStatement(object):
    def __init__(self, stack, next_token, next_start_pos):
        self.stack = stack
        self.next_token = next_token
        self.next_start_pos = next_start_pos

    @property
    def first_pos(self):
        first_type, nodes = self.stack[0]
        return nodes[0].start_pos

    @property
    def first_type(self):
        first_type, nodes = self.stack[0]
        return first_type


class Parser(object):
    """
    This class is used to parse a Python file, it then divides them into a
    class structure of different scopes.

    :param grammar: The grammar object of pgen2. Loaded by load_grammar.
    :param source: The codebase for the parser. Must be unicode.
    :param module_path: The path of the module in the file system, may be None.
    :type module_path: str
    :param top_module: Use this module as a parent instead of `self.module`.
    """
    def __init__(self, grammar, source, module_path=None, tokenizer=None):
        """
        This is the way I imagine a parser describing the init function
        """

        if not source.endswith('\n'):
            source += '\n'

        self._ast_mapping = {
            'expr_stmt': pt.ExprStmt,
            'classdef': pt.Class,
            'funcdef': pt.Function,
            'file_input': pt.SubModule,
            'import_name': pt.ImportName,
            'import_from': pt.ImportFrom,
            'break_stmt': pt.KeywordStatement,
            'continue_stmt': pt.KeywordStatement,
            'return_stmt': pt.ReturnStmt,
            'raise_stmt': pt.KeywordStatement,
            'yield_expr': pt.YieldExpr,
            'del_stmt': pt.KeywordStatement,
            'pass_stmt': pt.KeywordStatement,
            'global_stmt': pt.GlobalStmt,
            'nonlocal_stmt': pt.KeywordStatement,
            'assert_stmt': pt.AssertStmt,
            'if_stmt': pt.IfStmt,
            'with_stmt': pt.WithStmt,
            'for_stmt': pt.ForStmt,
            'while_stmt': pt.WhileStmt,
            'try_stmt': pt.TryStmt,
            'comp_for': pt.CompFor,
            'decorator': pt.Decorator,
            'lambdef': pt.Lambda,
            'old_lambdef': pt.Lambda,
            'lambdef_nocond': pt.Lambda,
        }

        self.global_names = []
        self._omit_dedent = 0

        # TODO do print absolute import detection here.
        #try:
        #    del python_grammar_no_print_statement.keywords["print"]
        #except KeyError:
        #    pass  # Doesn't exist in the Python 3 grammar.

        #if self.options["print_function"]:
        #    python_grammar = pygram.python_grammar_no_print_statement
        #else:
        self.used_names = {}
        self.scope_names_stack = [{}]
        self.error_statement_stacks = []
        # For fast parser
        self.position_modifier = pt.PositionModifier()
        p = PgenParser(grammar, self.convert_node, self.convert_leaf,
                       self.error_recovery)
        tokenizer = tokenizer or tokenize.source_tokens(source)
        self.module = p.parse(self._tokenize(tokenizer))

        self.module.used_names = self.used_names
        self.module.path = module_path
        self.module.set_global_names(self.global_names)
        self.module.error_statement_stacks = self.error_statement_stacks
        self.grammar_symbols = grammar.number2symbol

    def convert_node(self, grammar, type, children):
        """
        Convert raw node information to a Node instance.

        This is passed to the parser driver which calls it whenever a reduction of a
        grammar rule produces a new complete node, so that the tree is build
        strictly bottom-up.
        """
        symbol = grammar.number2symbol[type]
        try:
            new_node = self._ast_mapping[symbol](children)
        except KeyError:
            new_node = pt.Node(symbol, children)

        # We need to check raw_node always, because the same node can be
        # returned by convert multiple times.
        if symbol == 'global_stmt':
            self.global_names += new_node.get_global_names()
        elif isinstance(new_node, pt.Lambda):
            new_node.names_dict = self.scope_names_stack.pop()
        elif isinstance(new_node, (pt.ClassOrFunc, pt.Module)) \
                and symbol in ('funcdef', 'classdef', 'file_input'):
            # scope_name_stack handling
            scope_names = self.scope_names_stack.pop()
            if isinstance(new_node, pt.ClassOrFunc):
                n = new_node.name
                scope_names[n.value].remove(n)
                # Set the func name of the current node
                arr = self.scope_names_stack[-1].setdefault(n.value, [])
                arr.append(n)
            new_node.names_dict = scope_names
        elif isinstance(new_node, pt.CompFor):
            # The name definitions of comprehenions shouldn't be part of the
            # current scope. They are part of the comprehension scope.
            for n in new_node.get_defined_names():
                self.scope_names_stack[-1][n.value].remove(n)
        return new_node

    def convert_leaf(self, grammar, type, value, prefix, start_pos):
        #print('leaf', value, pytree.type_repr(type))
        if type == tokenize.NAME:
            if value in grammar.keywords:
                if value in ('def', 'class', 'lambda'):
                    self.scope_names_stack.append({})

                return pt.Keyword(self.position_modifier, value, start_pos, prefix)
            else:
                name = pt.Name(self.position_modifier, value, start_pos, prefix)
                # Keep a listing of all used names
                arr = self.used_names.setdefault(name.value, [])
                arr.append(name)
                arr = self.scope_names_stack[-1].setdefault(name.value, [])
                arr.append(name)
                return name
        elif type == token.STRING:
            return pt.String(self.position_modifier, value, start_pos, prefix)
        elif type == token.NUMBER:
            return pt.Number(self.position_modifier, value, start_pos, prefix)
        elif type in (token.NEWLINE, token.ENDMARKER):
            return pt.Whitespace(self.position_modifier, value, start_pos, prefix)
        else:
            return pt.Operator(self.position_modifier, value, start_pos, prefix)

    def error_recovery(self, grammar, stack, typ, value, start_pos, prefix,
                       add_token_callback):
        """
        This parser is written in a dynamic way, meaning that this parser
        allows using different grammars (even non-Python). However, error
        recovery is purely written for Python.
        """
        def current_suite(stack):
            # For now just discard everything that is not a suite or
            # file_input, if we detect an error.
            for index, (dfa, state, (typ, nodes)) in reversed(list(enumerate(stack))):
                # `suite` can sometimes be only simple_stmt, not stmt.
                symbol = grammar.number2symbol[typ]
                if symbol == 'file_input':
                    break
                elif symbol == 'suite' and len(nodes) > 1:
                    # suites without an indent in them get discarded.
                    break
                elif symbol == 'simple_stmt' and len(nodes) > 1:
                    # simple_stmt can just be turned into a Node, if there are
                    # enough statements. Ignore the rest after that.
                    break
            return index, symbol, nodes

        index, symbol, nodes = current_suite(stack)
        if symbol == 'simple_stmt':
            index -= 2
            (_, _, (typ, suite_nodes)) = stack[index]
            symbol = grammar.number2symbol[typ]
            suite_nodes.append(pt.Node(symbol, list(nodes)))
            # Remove
            nodes[:] = []
            nodes = suite_nodes
            stack[index]

        #print('err', token.tok_name[typ], repr(value), start_pos, len(stack), index)
        self._stack_removal(grammar, stack, index + 1, value, start_pos)
        if typ == token.INDENT:
            # For every deleted INDENT we got to delete a DEDENT as well.
            # Otherwise the parser will get into trouble and DEDENT too early.
            self._omit_dedent += 1

        if value in ('import', 'from', 'class', 'def', 'try', 'while', 'return'):
            # Those can always be new statements.
            add_token_callback(typ, value, prefix, start_pos)
        elif typ == token.DEDENT and symbol == 'suite':
            # Close the current suite, with DEDENT.
            # Note that this may cause some suites to not contain any
            # statements at all. This is contrary to valid Python syntax. We
            # keep incomplete suites in Jedi to be able to complete param names
            # or `with ... as foo` names. If we want to use this parser for
            # syntax checks, we have to check in a separate turn if suites
            # contain statements or not. However, a second check is necessary
            # anyway (compile.c does that for Python), because Python's grammar
            # doesn't stop you from defining `continue` in a module, etc.
            add_token_callback(typ, value, prefix, start_pos)

    def _stack_removal(self, grammar, stack, start_index, value, start_pos):
        def clear_names(children):
            for c in children:
                try:
                    clear_names(c.children)
                except AttributeError:
                    if isinstance(c, pt.Name):
                        try:
                            self.scope_names_stack[-1][c.value].remove(c)
                            self.used_names[c.value].remove(c)
                        except ValueError:
                            pass  # This may happen with CompFor.

        for dfa, state, node in stack[start_index:]:
            clear_names(children=node[1])

        failed_stack = []
        found = False
        for dfa, state, (typ, nodes) in stack[start_index:]:
            if nodes:
                found = True
            if found:
                symbol = grammar.number2symbol[typ]
                failed_stack.append((symbol, nodes))
            if nodes and nodes[0] in ('def', 'class', 'lambda'):
                self.scope_names_stack.pop()
        if failed_stack:
            err = ErrorStatement(failed_stack, value, start_pos)
            self.error_statement_stacks.append(err)

        stack[start_index:] = []

    def _tokenize(self, tokenizer):
        for typ, value, start_pos, prefix in tokenizer:
            if self._omit_dedent and typ == token.DEDENT:
                self._omit_dedent -= 1
                continue

            if typ == token.OP:
                typ = token.opmap[value]
            #print(start_pos, tokenize.tok_name[typ], repr(value))
            print(token.tok_name[typ], value, start_pos)
            yield typ, value, prefix, start_pos

    def __repr__(self):
        return "<%s: %s>" % (type(self).__name__, self.module)
