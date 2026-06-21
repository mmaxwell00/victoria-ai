import ast
import operator
from victoria.tools.registry import registry

_SAFE_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
    ast.USub: operator.neg,
    ast.UAdd: operator.pos,
}


def _eval_node(node: ast.AST) -> float:
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return node.value
    if isinstance(node, ast.BinOp):
        op_fn = _SAFE_OPS.get(type(node.op))
        if not op_fn:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op_fn(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op_fn = _SAFE_OPS.get(type(node.op))
        if not op_fn:
            raise ValueError(f"Unsupported operator: {type(node.op).__name__}")
        return op_fn(_eval_node(node.operand))
    raise ValueError(f"Unsupported expression type: {type(node).__name__}")


@registry.tool(
    name="calculate",
    description="Evaluate a mathematical expression. Supports +, -, *, /, //, %, ** (power).",
    parameters={
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "Math expression to evaluate, e.g. '(42 * 1.15) + 7' or '2 ** 10'",
            },
        },
        "required": ["expression"],
    },
)
def calculate(expression: str) -> str:
    try:
        tree = ast.parse(expression.strip(), mode="eval")
        result = _eval_node(tree.body)
        # Format: remove trailing zeros for clean output
        if isinstance(result, float) and result == int(result):
            return f"{expression} = {int(result)}"
        return f"{expression} = {result}"
    except Exception as exc:
        return f"Calculation error: {exc}"
