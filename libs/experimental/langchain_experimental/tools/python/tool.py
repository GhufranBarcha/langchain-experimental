"""A tool for running python code in a REPL."""

import ast
import base64
import re
import sys
from contextlib import redirect_stdout
from io import BytesIO, StringIO
from typing import Any, Dict, Optional, Type

from langchain_core.callbacks.manager import (
    AsyncCallbackManagerForToolRun,
    CallbackManagerForToolRun,
)
from langchain_core.runnables.config import run_in_executor
from langchain_core.tools import BaseTool
from pydantic import BaseModel, Field, model_validator

from langchain_experimental.utilities.python import PythonREPL


def _get_default_python_repl() -> PythonREPL:
    return PythonREPL(_globals=globals(), _locals=None)


def sanitize_input(query: str) -> str:
    """Sanitize input to the python REPL.

    Remove whitespace, backtick & python (if llm mistakes python console as terminal)

    Args:
        query: The query to sanitize

    Returns:
        str: The sanitized query
    """

    # Removes `, whitespace & python from start
    query = re.sub(r"^(\s|`)*(?i:python)?\s*", "", query)
    # Removes whitespace & ` from end
    query = re.sub(r"(\s|`)*$", "", query)
    return query


class PythonREPLTool(BaseTool):
    """Tool for running python code in a REPL."""

    name: str = "Python_REPL"
    description: str = (
        "A Python shell. Use this to execute python commands. "
        "Input should be a valid python command. "
        "If you want to see the output of a value, you should print it out "
        "with `print(...)`."
    )
    python_repl: PythonREPL = Field(default_factory=_get_default_python_repl)
    sanitize_input: bool = True

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> Any:
        """Use the tool."""
        if self.sanitize_input:
            query = sanitize_input(query)
        return self.python_repl.run(query)

    async def _arun(
        self,
        query: str,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> Any:
        """Use the tool asynchronously."""
        if self.sanitize_input:
            query = sanitize_input(query)

        return await run_in_executor(None, self.run, query)


class PythonInputs(BaseModel):
    """Python inputs."""

    query: str = Field(description="code snippet to run")


class PythonAstREPLTool(BaseTool):
    """Tool for running python code in a REPL."""

    name: str = "python_repl_ast"
    description: str = (
        "A Python shell. Use this to execute python commands. "
        "Input should be a valid python command. "
        "When using this tool, sometimes output is abbreviated - "
        "make sure it does not look abbreviated before using it in your answer."
    )
    globals: Optional[Dict] = Field(default_factory=dict)  # type: ignore[arg-type]
    locals: Optional[Dict] = Field(default_factory=dict)  # type: ignore[arg-type]
    sanitize_input: bool = True
    args_schema: Type[BaseModel] = PythonInputs

    @model_validator(mode="before")
    @classmethod
    def validate_python_version(cls, values: Dict) -> Any:
        """Validate valid python version."""
        if sys.version_info < (3, 9):
            raise ValueError(
                "This tool relies on Python 3.9 or higher "
                "(as it uses new functionality in the `ast` module, "
                f"you have Python version: {sys.version}"
            )
        return values

    def _run(
        self,
        query: str,
        run_manager: Optional[CallbackManagerForToolRun] = None,
    ) -> Any:
        """Use the tool."""
        try:
            if self.sanitize_input:
                query = sanitize_input(query)
            tree = ast.parse(query)
            module = ast.Module(tree.body[:-1], type_ignores=[])
            exec(ast.unparse(module), self.globals, self.locals)  # type: ignore
            module_end = ast.Module(tree.body[-1:], type_ignores=[])
            module_end_str = ast.unparse(module_end)  # type: ignore
            io_buffer = StringIO()
            try:
                with redirect_stdout(io_buffer):
                    ret = eval(module_end_str, self.globals, self.locals)
                    if ret is None:
                        return io_buffer.getvalue()
                    else:
                        # Try to detect and handle plot objects
                        # Matplotlib
                        if "matplotlib.figure.Figure" in str(type(ret)):
                            buf = BytesIO()
                            ret.savefig(buf, format="png")
                            buf.seek(0)
                            img_str = base64.b64encode(buf.read()).decode()
                            return {"image": img_str}
                        # Plotly
                        if "plotly.graph_objs._figure.Figure" in str(type(ret)):
                            try:
                                img_bytes = ret.to_image(format="png")
                                img_str = base64.b64encode(img_bytes).decode()
                                return {"image": img_str}
                            except Exception as e:
                                return f"Error converting plotly figure to image: {e}"
                        return ret
            except Exception:
                with redirect_stdout(io_buffer):
                    exec(module_end_str, self.globals, self.locals)
                return io_buffer.getvalue()
        except Exception as e:
            return "{}: {}".format(type(e).__name__, str(e))

    async def _arun(
        self,
        query: str,
        run_manager: Optional[AsyncCallbackManagerForToolRun] = None,
    ) -> Any:
        """Use the tool asynchronously."""

        return await run_in_executor(None, self._run, query)
