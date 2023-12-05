#!/usr/bin/env python 3.11.0
# -*-coding:utf-8 -*-
# @Author  : Shuang (Twist) Song
# @Contact   : SongshGeo@gmail.com
# GitHub   : https://github.com/SongshGeo
# Website: https://cv.songshgeo.com/

from __future__ import annotations

from itertools import combinations
from typing import TYPE_CHECKING, Iterable, List, Tuple, overload

import numpy as np

from abses.errors import ABSESpyError
from abses.tools.func import make_list

if TYPE_CHECKING:
    from abses.actor import Actor
    from abses.main import MainModel
    from abses.sequences import ActorsList


class ListRandom:
    """Create a random generator from an `ActorsList`"""

    def __init__(self, model: MainModel, actors: ActorsList) -> None:
        self.model = model
        self.actors = actors
        self.seed = getattr(model, "_seed")
        self.generator = np.random.default_rng(seed=int(self.seed))

    def _to_actors_list(self, objs: Iterable) -> ActorsList:
        from abses.sequences import ActorsList

        return ActorsList(self.model, objs=objs)

    @overload
    def clean_p(self, p: str) -> np.ndarray:
        ...

    def clean_p(self, prob: np.ndarray) -> np.ndarray:
        """Clean the main"""
        if isinstance(prob, str):
            prob = self.actors.array(attr=prob)
        prob = np.array(make_list(prob))
        length = len(prob)
        prob = np.nan_to_num(prob)
        prob[prob < 0] = 0.0
        total = prob.sum()
        prob = prob / total if total else np.repeat(1 / length, length)
        return prob

    def choice(
        self,
        size: int = 1,
        prob: np.ndarray | None | str = None,
        replace: bool = False,
        as_list: bool = False,
    ) -> Actor | ActorsList:
        """Randomly choose one or more actors from the current self object.

        Parameters:
            size:
                The number of actors to choose. Defaults to 1.
            prob:
                A list of probabilities for each actor to be chosen.
                If None, all actors have equal probability. Defaults to None.
            replace:
                Whether to sample with replacement. Defaults to True.
            as_list:
                Whether to return the result as a list of actors. Defaults to False.

        Returns:
            An Actor or an ActorList of multiple actors.

        Notes:
            Given the parameter set size=1 and as_list=False, a single Actor object is returned.
            Given the parameter set size>1 and as_list=False, a Self (ActorsList) object is returned.

        Raises:
            ValueError:
                If size is not a positive integer.
            ABSESpyError:
                Not enough actors to choose in this `ActorsList`.
        """
        instances_num = len(self.actors)
        if not instances_num or (instances_num < size & ~replace):
            raise ABSESpyError(
                f"Trying to choose {size} actors from an `ActorsList` {self.actors}."
            )
        if prob is not None:
            prob = self.clean_p(prob=prob)
        chosen = self.generator.choice(
            self.actors, size=size, replace=replace, p=prob
        )
        return (
            chosen[0]
            if size == 1 and not as_list
            else self._to_actors_list(chosen)
        )

    def link(self, link: str, p: float = 1.0) -> List[Tuple[Actor, Actor]]:
        """Random build links between actors."""
        linked_combs = []
        for actor1, actor2 in list(combinations(self.actors, 2)):
            if np.random.random() < p:
                actor1.link_to(actor2, link=link, mutual=True)
                linked_combs.append((actor1, actor2))
        return linked_combs
