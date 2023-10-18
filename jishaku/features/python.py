# -*- coding: utf-8 -*-

"""
jishaku.features.python
~~~~~~~~~~~~~~~~~~~~~~~~

The jishaku Python evaluation/execution commands.

:copyright: (c) 2021 Devon (Gorialis) R
:license: MIT, see LICENSE for more details.

"""

import asyncio
import collections
import inspect
import io
import sys
import time
import typing

import discord

from jishaku.codeblocks import Codeblock, codeblock_converter
from jishaku.exception_handling import ReplResponseReactor
from jishaku.features.baseclass import Feature
from jishaku.flags import Flags
from jishaku.formatting import MultilineFormatter
from jishaku.functools import AsyncSender
from jishaku.math import format_bargraph, format_stddev
from jishaku.paginators import PaginatorInterface, WrappedPaginator, use_file_check
from jishaku.repl import AsyncCodeExecutor, Scope, all_inspections, create_tree, disassemble, get_adaptive_spans, get_var_dict_from_ctx
from jishaku.types import ContextA

try:
    import line_profiler  # type: ignore
except ImportError:
    line_profiler = None


class PythonFeature(Feature):
    """
    Feature containing the Python-related commands
    """

    def __init__(self, *args: typing.Any, **kwargs: typing.Any):
        super().__init__(*args, **kwargs)
        self._scope = Scope()
        self.retain = Flags.RETAIN
        self.last_result: typing.Any = None

    @property
    def scope(self):
        """
        Gets a scope for use in REPL.

        If retention is on, this is the internal stored scope,
        otherwise it is always a new Scope.
        """

        if self.retain:
            return self._scope
        return Scope()



    async def jsk_python_result_handling(self, ctx: ContextA, result: typing.Any):  # pylint: disable=too-many-return-statements
        """
        Determines what is done with a result when it comes out of jsk py.
        This allows you to override how this is done without having to rewrite the command itself.
        What you return is what gets stored in the temporary _ variable.
        """

        if isinstance(result, discord.Message):
            return await ctx.send(f"<Message <{result.jump_url}>>")

        if isinstance(result, discord.File):
            return await ctx.send(file=result)

        if isinstance(result, discord.Embed):
            return await ctx.send(embed=result)

        if isinstance(result, PaginatorInterface):
            return await result.send_to(ctx)

        if not isinstance(result, str):
            # repr all non-strings
            result = repr(result)

        # Eventually the below handling should probably be put somewhere else
        if len(result) <= 2000:
            if result.strip() == '':
                result = "\u200b"

            if self.bot.http.token:
                result = result.replace(self.bot.http.token, "[token omitted]")

            return await ctx.send(
                result,
                allowed_mentions=discord.AllowedMentions.none()
            )

        if use_file_check(ctx, len(result)):  # File "full content" preview limit
            # Discord's desktop and web client now supports an interactive file content
            #  display for files encoded in UTF-8.
            # Since this avoids escape issues and is more intuitive than pagination for
            #  long results, it will now be prioritized over PaginatorInterface if the
            #  resultant content is below the filesize threshold
            return await ctx.send(file=discord.File(
                filename="output.py",
                fp=io.BytesIO(result.encode('utf-8'))
            ))

        # inconsistency here, results get wrapped in codeblocks when they are too large
        #  but don't if they're not. probably not that bad, but noting for later review
        paginator = WrappedPaginator(prefix='```py', suffix='```', max_size=1980)

        paginator.add_line(result)

        interface = PaginatorInterface(ctx.bot, paginator, owner=ctx.author)
        return await interface.send_to(ctx)

    def jsk_python_get_convertables(self, ctx: ContextA) -> typing.Tuple[typing.Dict[str, typing.Any], typing.Dict[str, str]]:
        """
        Gets the arg dict and convertables for this scope.

        The arg dict contains the 'locals' to be propagated into the REPL scope.
        The convertables are string->string conversions to be attempted if the code fails to parse.
        """

        arg_dict = get_var_dict_from_ctx(ctx, Flags.SCOPE_PREFIX)
        arg_dict["_"] = self.last_result
        convertables: typing.Dict[str, str] = {}

        if getattr(ctx, 'interaction', None) is None:
            for index, user in enumerate(ctx.message.mentions):
                arg_dict[f"__user_mention_{index}"] = user
                convertables[user.mention] = f"__user_mention_{index}"

            for index, channel in enumerate(ctx.message.channel_mentions):
                arg_dict[f"__channel_mention_{index}"] = channel
                convertables[channel.mention] = f"__channel_mention_{index}"

            for index, role in enumerate(ctx.message.role_mentions):
                arg_dict[f"__role_mention_{index}"] = role
                convertables[role.mention] = f"__role_mention_{index}"

        return arg_dict, convertables
 

