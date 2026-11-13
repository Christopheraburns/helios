"""``helios eval``: run an evaluation suite against the configured LLM (helios.commands)."""
from __future__ import annotations

import argparse
import json
from typing import TYPE_CHECKING

from helios_cortex.cli import CommandError, add_caller_argument

if TYPE_CHECKING:
    from helios_cortex.cli import CommandContext

SUMMARY = (("answer_accuracy", "accuracy", "{:.0%}"), ("completion_rate", "completed", "{:.0%}"),
           ("tool_calls", "tool calls", "{:g}"), ("invalid_call_rate", "invalid", "{:.0%}"),
           ("redundant_call_rate", "redundant", "{:.0%}"), ("grounding_rate", "grounded", "{:.0%}"),
           ("tokens_per_correct_answer", "tokens/correct", "{:,.0f}"))


class EvalCommand:
    help = "ask an evaluation suite's questions of the LLM through the Helios tools and score the answers"

    def configure(self, parser: argparse.ArgumentParser) -> None:
        parser.add_argument("suite", nargs="?", default="tpcds",
                            help="a suite id, built in or saved in <data_dir>/state/eval-suites (default: tpcds)")
        parser.add_argument("--version", dest="suite_version", metavar="VERSION",
                            help="the suite version (default: the latest)")
        parser.add_argument("--suite-file", metavar="FILE", help="read the suite from FILE instead")
        parser.add_argument("--list", action="store_true", help="list the available suites and exit")
        parser.add_argument("--model", metavar="ID", help="the published model to answer from (default: the suite's)")
        parser.add_argument("--candidate-model", metavar="NAME",
                            help="also run the configured LLM provider with model NAME and compare the two")
        parser.add_argument("--repetitions", type=int, default=1, metavar="N", help="ask each question N times")
        parser.add_argument("--max-tool-rounds", type=int, default=6, metavar="N",
                            help="the most LLM rounds per answer (default 6)")
        parser.add_argument("--question", action="append", metavar="ID", help="only this question (repeat for more)")
        parser.add_argument("--json", action="store_true", help="write the full report as JSON")
        add_caller_argument(parser, "answer and run the reference queries")

    def run(self, args: argparse.Namespace, context: CommandContext) -> int:
        from helios_serve import evaluation
        from helios_serve.stores import Stores, single_user_policy

        settings = context.settings
        directory = evaluation.suite_directory(settings)
        if args.list:
            for suite in evaluation.list_suites(directory):
                context.echo(f"{suite['id']}  {suite['version']}  {len(suite['questions'])} questions"
                             + (f"  (model {suite['model_id']})" if suite.get("model_id") else ""))
            return 0
        try:
            suite = (evaluation.read_suite(args.suite_file) if args.suite_file
                     else evaluation.load_suite(args.suite, args.suite_version, directory=directory))
        except (LookupError, evaluation.EvaluationError) as exc:
            raise CommandError(str(exc.args[0]) if exc.args else str(exc)) from exc
        if args.question:
            wanted = set(args.question)
            suite = {**suite, "questions": [q for q in suite["questions"] if q["id"] in wanted]}
            missing = wanted - {q["id"] for q in suite["questions"]}
            if missing:
                raise CommandError(f"no question {', '.join(sorted(missing))} in suite {suite['id']}")

        try:
            from helios_cortex.llm import llm_from_settings
        except ModuleNotFoundError as exc:
            raise CommandError("helios eval needs an LLM client: pip install 'helios-serve[eval]'") from exc
        llm = llm_from_settings(settings.llm)
        if llm is None:
            raise CommandError("no LLM is configured; set [llm] provider and its key in helios.toml")
        variants = {"baseline": llm}
        if args.candidate_model:
            candidate = llm_from_settings(settings.llm.model_copy(update={"model": args.candidate_model}))
            if candidate is None:
                raise CommandError(f"no LLM client for the candidate model {args.candidate_model!r}")
            variants["candidate"] = candidate

        caller = context.caller(args.caller)
        # Without --as the command's own user runs it with the configured credentials (as
        # `helios harvest` does); with --as the warehouse enforces that user's access.
        stores = Stores.from_settings(settings, **({} if caller else {"data_policy": single_user_policy()}))
        try:
            report = evaluation.run_evaluation(
                suite, stores, variants, caller=caller,
                model=args.model, repetitions=args.repetitions, max_tool_rounds=args.max_tool_rounds)
        except evaluation.EvaluationError as exc:
            raise CommandError(str(exc)) from exc

        if args.json:
            context.echo(json.dumps(report.as_dict(), indent=2, default=str))
            return 0
        context.echo(f"suite {report.suite_id} {report.suite_version}, model {report.model or '(first published)'}"
                     + (" (cancelled)" if report.cancelled else ""))
        for result in report.results:
            mark = "ok  " if result.accurate else "MISS"
            context.echo(f"  {mark} {result.question_id} {result.variant} #{result.repetition}: "
                         f"{result.answer.termination}, {result.metrics['tool_calls']} tool calls")
        for variant, metrics in report.metrics.items():
            name = f"{variant} ({variants[variant].model})"
            context.echo(f"{name}: " + ", ".join(f"{label} {fmt.format(metrics[key])}" for key, label, fmt in SUMMARY))
        return 0
