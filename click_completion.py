#!/usr/bin/env python
# -*- coding:utf-8 -*-

from __future__ import print_function, absolute_import

import os
import re
import sys
import shlex

import six

from click import echo, MultiCommand, Option, Argument, ParamType

__version__ = '0.1.0'

FISH_COMPLETION_SCRIPT = '''
complete --command %(script_names)s --arguments "(env %(autocomplete_var)s=complete-fish COMMANDLINE=(commandline -cp) %(script_names)s)" -f
'''

ZSH_COMPLETION_SCRIPT = '''
#compdef %(script_names)s
_%(script_names)s() {
  eval $(env COMMANDLINE="${words[1,$CURRENT]}" %(autocomplete_var)s=complete-zsh %(script_names)s)
}
if [[ "$(basename ${(%%):-%%x})" != "_%(script_names)s" ]]; then
  autoload -U compinit && compinit
  compdef _%(script_names)s %(script_names)s
fi
'''

POWERSHELL_COMPLETION_SCRIPT = '''
if ((Test-Path Function:\TabExpansion) -and -not (Test-Path Function:\%(complete_backup)s)) {
    Rename-Item Function:\TabExpansion %(complete_backup)s
}

function TabExpansion($line, $lastWord) {
    $lastBlock = [regex]::Split($line, '[|;]')[-1].TrimStart()
    $aliases = @("%(script_names)s") + @(Get-Alias | where { $_.Definition -eq "%(script_names)s" } | select -Exp Name)
    $aliasPattern = "($($aliases -join '|'))"
    if($lastBlock -match "^$aliasPattern ") {
        $Env:%(autocomplete_var)s = "complete-powershell"
        $Env:COMMANDLINE = "$lastBlock"
        (%(script_names)s) | ? {$_.trim() -ne "" }
        Remove-Item Env:%(autocomplete_var)s
        Remove-Item Env:COMMANDLINE
    }
    elseif (Test-Path Function:\%(complete_backup)s) {
        # Fall back on existing tab expansion
        %(complete_backup)s $line $lastWord
    }
}
'''

COMPLETION_SCRIPT = '''
%(complete_func)s() {
    local IFS=$'\\t'
    COMPREPLY=( $( env COMP_WORDS="${COMP_WORDS[*]}" \\
                   COMP_CWORD=$COMP_CWORD \\
                   %(autocomplete_var)s=complete $1 ) )
    return 0
}

complete -F %(complete_func)s -o default %(script_names)s
'''

_invalid_ident_char_re = re.compile(r'[^a-zA-Z0-9_]')


def get_bash_completion_script(prog_name, complete_var):
    cf_name = _invalid_ident_char_re.sub('', prog_name.replace('-', '_'))
    return (COMPLETION_SCRIPT % {
        'complete_func': '_%s_completion' % cf_name,
        'script_names': prog_name,
        'autocomplete_var': complete_var,
    }).strip() + ';'


def get_fish_completion_script(prog_name, complete_var):
    return (FISH_COMPLETION_SCRIPT % {
        'script_names': prog_name,
        'autocomplete_var': complete_var,
    }).strip() + ';'


def get_zsh_completion_script(prog_name, complete_var):
    cf_name = _invalid_ident_char_re.sub('', prog_name.replace('-', '_'))
    return (ZSH_COMPLETION_SCRIPT % {
        'complete_func': '_%s' % cf_name,
        'script_names': prog_name,
        'autocomplete_var': complete_var,
    }).strip() + ';'


def get_powershell_completion_script(prog_name, complete_var):
    return (POWERSHELL_COMPLETION_SCRIPT % {
        'script_names': prog_name,
        'autocomplete_var': complete_var,
        'complete_backup': prog_name.lower().replace('-', '_') + 'TabExpansionBackup',
    }).strip() + ';'


def resolve_ctx(cli, prog_name, args):
    ctx = cli.make_context(prog_name, list(args), resilient_parsing=True)
    while ctx.args + ctx.protected_args and isinstance(ctx.command, MultiCommand):
        a = ctx.protected_args + ctx.args
        cmd = ctx.command.get_command(ctx, a[0])
        if cmd is None:
            return None
        ctx = cmd.make_context(a[0], a[1:], parent=ctx, resilient_parsing=True)
    return ctx


def startswith(string, incomplete):
    """Returns True when string starts with incomplete

    It might be overridden with a fuzzier version - for example a case insensitive version"""
    return string.startswith(incomplete)


def get_choices(cli, prog_name, args, incomplete):
    ctx = resolve_ctx(cli, prog_name, args)
    if ctx is None:
        return

    optctx = None
    if args:
        for param in ctx.command.get_params(ctx):
            if isinstance(param, Option) and not param.is_flag and args[-1] in param.opts + param.secondary_opts:
                optctx = param

    choices = []
    if optctx:
        choices += [c if isinstance(c, tuple) else (c, None) for c in optctx.type.complete(ctx, incomplete)]
    elif incomplete and not incomplete[:1].isalnum():
        for param in ctx.command.get_params(ctx):
            if not isinstance(param, Option):
                continue
            for opt in param.opts:
                if startswith(opt, incomplete):
                    choices.append((opt, param.help))
            for opt in param.secondary_opts:
                if startswith(opt, incomplete):
                    # don't put the doc so fish won't group the primary and
                    # and secondary options
                    choices.append((opt, None))
    elif isinstance(ctx.command, MultiCommand):
        for name in ctx.command.list_commands(ctx):
            if startswith(name, incomplete):
                choices.append((name, ctx.command.get_command_short_help(ctx, name)))
    else:
        for param in ctx.command.get_params(ctx):
            if isinstance(param, Argument):
                choices += [c if isinstance(c, tuple) else (c, None) for c in param.type.complete(ctx, incomplete)]

    for item, help in choices:
        yield (item, help)


def split_args(line):
    """Version of shlex.split that silently accept incomplete strings."""
    lex = shlex.shlex(line, posix=True)
    lex.whitespace_split = True
    lex.commenters = ''
    res = []
    try:
        while True:
            res.append(next(lex))
    except ValueError:  # No closing quotation
        pass
    except StopIteration:  # End of loop
        pass
    if lex.token:
        res.append(lex.token)
    return res


def decode_args(strings):
    res = []
    for s in strings:
        s = split_args(s)
        s = s[0] if s else ''
        res.append(s)
    return res


def do_bash_complete(cli, prog_name):
    comp_words = os.environ['COMP_WORDS']
    try:
        cwords = shlex.split(comp_words)
        quoted = False
    except ValueError:  # No closing quotation
        cwords = split_args(comp_words)
        quoted = True
    cword = int(os.environ['COMP_CWORD'])
    args = cwords[1:cword]
    try:
        incomplete = cwords[cword]
    except IndexError:
        incomplete = ''
    choices = get_choices(cli, prog_name, args, incomplete)

    if quoted:
        echo('\t'.join(opt for opt, _ in choices), nl=False)
    else:
        echo('\t'.join(re.sub(r"""([\s\\"'])""", r'\\\1', opt) for opt, _ in choices), nl=False)

    return True


def do_fish_complete(cli, prog_name):
    commandline = os.environ['COMMANDLINE']
    args = split_args(commandline)[1:]
    if args and not commandline.endswith(' '):
        incomplete = args[-1]
        args = args[:-1]
    else:
        incomplete = ''

    for item, help in get_choices(cli, prog_name, args, incomplete):
        if help:
            echo("%s\t%s" % (item, help))
        else:
            echo(item)

    return True


def do_zsh_complete(cli, prog_name):
    commandline = os.environ['COMMANDLINE']
    args = split_args(commandline)[1:]
    if args and not commandline.endswith(' '):
        incomplete = args[-1]
        args = args[:-1]
    else:
        incomplete = ''

    def escape(s):
        return s.replace('"', '""').replace("'", "''").replace('$', '\\$')
    res = []
    for item, help in get_choices(cli, prog_name, args, incomplete):
        if help:
            res.append('"%s"\:"%s"' % (escape(item), escape(help)))
        else:
            res.append('"%s"' % escape(item))
    echo("_arguments '*: :((%s))'" % '\n'.join(res))

    return True


def do_powershell_complete(cli, prog_name):
    commandline = os.environ['COMMANDLINE']
    args = split_args(commandline)[1:]
    quote = single_quote
    incomplete = ''
    if args and not commandline.endswith(' '):
        incomplete = args[-1]
        args = args[:-1]
        quote_pos = commandline.rfind(incomplete) - 1
        if quote_pos >= 0 and commandline[quote_pos] == '"':
            quote = double_quote

    for item, help in get_choices(cli, prog_name, args, incomplete):
        echo(quote(item))

    return True


find_unsafe = re.compile(r'[^\w@%+=:,./-]').search


def single_quote(s):
    """Return a shell-escaped version of the string *s*."""
    if not s:
        return "''"
    if find_unsafe(s) is None:
        return s

    # use single quotes, and put single quotes into double quotes
    # the string $'b is then quoted as '$'"'"'b'
    return "'" + s.replace("'", "'\"'\"'") + "'"


def double_quote(s):
    '''Return a shell-escaped version of the string *s*.'''
    if not s:
        return '""'
    if find_unsafe(s) is None:
        return s

    # use double quotes, and put double quotes into single quotes
    # the string $"b is then quoted as "$"'"'"b"
    return '"' + s.replace('"', '"\'"\'"') + '"'


# extend click completion features

def param_type_complete(self, ctx, incomplete):
    return []


def choice_complete(self, ctx, incomplete):
    return [c for c in self.choices if c.startswith(incomplete)]


def multicommand_get_command_short_help(self, ctx, cmd_name):
    return self.get_command(ctx, cmd_name).short_help


def _shellcomplete(cli, prog_name, complete_var=None):
    """Internal handler for the bash completion support."""
    if complete_var is None:
        complete_var = '_%s_COMPLETE' % (prog_name.replace('-', '_')).upper()
    complete_instr = os.environ.get(complete_var)
    if not complete_instr:
        return

    if complete_instr == 'source':
        echo(get_bash_completion_script(prog_name, complete_var))
    elif complete_instr == 'complete':
        do_bash_complete(cli, prog_name)
    elif complete_instr == 'source-fish':
        echo(get_fish_completion_script(prog_name, complete_var))
    elif complete_instr == 'complete-fish':
        do_fish_complete(cli, prog_name)
    elif complete_instr == 'source-powershell':
        echo(get_powershell_completion_script(prog_name, complete_var))
    elif complete_instr == 'complete-powershell':
        do_powershell_complete(cli, prog_name)
    elif complete_instr == 'source-zsh':
        echo(get_zsh_completion_script(prog_name, complete_var))
    elif complete_instr == 'complete-zsh':
        do_zsh_complete(cli, prog_name)
    sys.exit()


def init():
    """patch click to support fish completion"""
    import click
    click.types.ParamType.complete = param_type_complete
    click.types.Choice.complete = choice_complete
    click.core.MultiCommand.get_command_short_help = multicommand_get_command_short_help
    click.core._bashcomplete = _shellcomplete


class DocumentedChoice(ParamType):
    """The choice type allows a value to be checked against a fixed set of
    supported values.  All of these values have to be strings. Each value may
    be associated to a help message that will be display in the error message
    and during the completion.
    """
    name = 'choice'

    def __init__(self, choices):
        self.choices = dict(choices)

    def get_metavar(self, param):
        return '[%s]' % '|'.join(self.choices.keys())

    def get_missing_message(self, param):
        formated_choices = ['{:<12} {}'.format(k, self.choices[k] or '') for k in sorted(self.choices.keys())]
        return 'Choose from\n  ' + '\n  '.join(formated_choices)

    def convert(self, value, param, ctx):
        # Exact match
        if value in self.choices:
            return value

        # Match through normalization
        if ctx is not None and \
           ctx.token_normalize_func is not None:
            value = ctx.token_normalize_func(value)
            for choice in self.choices:
                if ctx.token_normalize_func(choice) == value:
                    return choice

        self.fail('invalid choice: %s. %s' %
                  (value, self.get_missing_message(param)), param, ctx)

    def __repr__(self):
        return 'DocumentedChoice(%r)' % list(self.choices.keys())

    def complete(self, ctx, incomplete):
        return [(c, v) for c, v in six.iteritems(self.choices) if startswith(c, incomplete)]