"""
ModuleGraph：模块依赖图。

- 节点注册时声明 provides（自己产出的模块）与 depends_on（上游模块）。
- 当上游模块被修改/重生时，图会把下游模块从 ready 标为 stale。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from threading import RLock
from typing import Dict, Iterable, List, Optional, Set

from .module_status import ModuleStatus


@dataclass
class ModuleNodeSpec:
    module_id: str
    provides_by: str
    depends_on: List[str] = field(default_factory=list)


class ModuleGraph:
    def __init__(self) -> None:
        self._nodes: Dict[str, ModuleNodeSpec] = {}
        self._lock = RLock()

    def register(self, spec: ModuleNodeSpec) -> None:
        with self._lock:
            self._nodes[spec.module_id] = spec

    def register_many(self, specs: Iterable[ModuleNodeSpec]) -> None:
        for s in specs:
            self.register(s)

    def get(self, module_id: str) -> Optional[ModuleNodeSpec]:
        with self._lock:
            return self._nodes.get(module_id)

    def descendants(self, module_id: str) -> Set[str]:
        """返回所有依赖 module_id 的下游模块（传递闭包）。"""
        with self._lock:
            result: Set[str] = set()
            direct_children = {
                other for other, spec in self._nodes.items() if module_id in spec.depends_on
            }
            stack: List[str] = list(direct_children)
            while stack:
                cur = stack.pop()
                if cur in result:
                    continue
                result.add(cur)
                for other, spec in self._nodes.items():
                    if cur in spec.depends_on and other not in result:
                        stack.append(other)
            return result

    def cascade_mark_stale(
        self,
        changed_module_id: str,
        module_status_lookup,
    ) -> List[str]:
        """
        将下游 ready 模块标记为 stale。

        module_status_lookup: Callable[[str], Optional[ModuleStatus]]
        返回本次被标 stale 的模块 id 列表。
        """
        marked: List[str] = []
        for dep_id in self.descendants(changed_module_id):
            current = module_status_lookup(dep_id)
            if current == ModuleStatus.READY:
                marked.append(dep_id)
        return marked

    def list_nodes(self) -> Dict[str, ModuleNodeSpec]:
        with self._lock:
            return dict(self._nodes)


module_graph = ModuleGraph()
