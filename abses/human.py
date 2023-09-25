#!/usr/bin/env python 3.11.0
# -*-coding:utf-8 -*-
# @Author  : Shuang Song
# @Contact   : SongshGeo@gmail.com
# GitHub   : https://github.com/SongshGeo
# Website: https://cv.songshgeo.com/

from typing import Callable, Dict, TypeAlias, Union

import networkx as nx
from omegaconf import DictConfig

from abses.actor import Actor

from .container import AgentsContainer
from .modules import CompositeModule, Module
from .sequences import ActorsList, Selection

Actors: TypeAlias = Union[ActorsList, Selection, Actor]
Trigger: TypeAlias = Union[str, Callable]


class HumanModule(Module):
    """基本的人类模块"""

    def __init__(self, model, name=None):
        Module.__init__(self, model, name)
        self._agents = AgentsContainer(model)
        self._collections: Dict[str, Selection] = DictConfig({})
        self._rules: Dict[str, Trigger] = DictConfig({})

    def __getattr__(self, name):
        if name[0] == "_" or name not in self._collections:
            return super().__getattr__(name)
        selection = self._collections[name]
        return self.actors.select(selection)

    @property
    def agents(self) -> AgentsContainer:
        """所有的主体筛选器"""
        return self._agents

    @property
    def actors(self) -> ActorsList:
        """所有的行动者"""
        return self.agents.to_list()

    def define(self, name: str, selection: Selection) -> ActorsList:
        """定义一次主体查询"""
        selected = self.actors.select(selection)
        self._collections[name] = selection
        return selected

    # def rule(self, actors: Actors, when: Selection, then: Trigger, name: Optional[str] = None):
    #     if name is None:
    #         pass
    #     self.define(name=name)
    #     actors_to_trigger = actors.select(when)
    #     results = actors_to_trigger.trigger(then)
    #     return actors_to_trigger, results

    def arena(self, actor_1: Actors, actor_2: Actors, interaction: Trigger):
        """互动情景"""
        actor_1.trigger(interaction, actor_2)
        actor_2.trigger(interaction, actor_1)

    def require(self, attr: str) -> object:
        """请求变量"""
        return self.mediator.transfer_request(self, attr)


class BaseHuman(CompositeModule, HumanModule):
    """基本的人类模块"""

    def __init__(self, model, name="human"):
        HumanModule.__init__(self, model, name)
        CompositeModule.__init__(self, model, name=name)
        self._bipartite_graphs: Dict[str, nx.Graph] = {}
        self._direct_graphs: Dict[str, nx.Graph] = {}


#     def mock(self, agents, attrs, how="attr"):
#         tutors = self.to_agents(agents.tutor.now)
#         for attr in make_list(attrs):
#             values = tutors.array(attr, how)
#             agents.update(attr, values)


# def skip_if_close(func):
#     def skip_module_method(self, *args, **kwargs):
#         if self.opening:
#             func(self, *args, **kwargs)
#         else:
#             if self.log_flag:
#                 self.logger.warning(f"{self}.")

#     return skip_module_method
