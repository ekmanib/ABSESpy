#!/usr/bin/env python 3.11.0
# -*-coding:utf-8 -*-
# @Author  : Shuang Song
# @Contact   : SongshGeo@gmail.com
# GitHub   : https://github.com/SongshGeo
# Website: https://cv.songshgeo.com/

"""
The spatial module.
"""

from __future__ import annotations

import copy
import functools
import itertools
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Sequence,
    Set,
    Type,
    Union,
    cast,
    overload,
)

try:
    from typing import Self
except ImportError:
    from typing_extensions import Self

import mesa_geo as mg
import numpy as np
import pyproj
import rasterio as rio
import rioxarray
import xarray as xr
from loguru import logger
from mesa.space import Coordinate, accept_tuple_argument
from mesa_geo.raster_layers import RasterBase
from rasterio import mask
from rasterio.warp import calculate_default_transform, transform_bounds
from shapely import Geometry

from abses.modules import CompositeModule, Module
from abses.random import ListRandom

from .cells import PatchCell
from .errors import ABSESpyError
from .sequences import ActorsList

if TYPE_CHECKING:
    from abses.main import MainModel

DEFAULT_WORLD = {
    "width": 10,
    "height": 10,
    "resolution": 1,
}
CRS = "epsg:4326"


class PatchModule(Module, RasterBase):
    """
    The spatial sub-module base class.
    Inherit from this class to create a submodule.
    [This tutorial](../tutorial/beginner/organize_model_structure.ipynb) shows the model structure.
    This is also a raster layer, inherited from the 'mesa-geo.RasterLayer' class.
    ABSESpy extends this class, so it can:
    1. place agents (by `_CellAgentsContainer` class.)
    2. work with `xarray`, `rasterio` packages for better data I/O workflow.

    Attributes:
        cell_properties:
            The accessible attributes of cells stored in this layer.
            When a `PatchCell`'s method is decorated by `raster_attribute`,
            it should be appeared here as a property attribute.
        attributes:
            All accessible attributes from this layer,
            including cell_properties.
        shape2d:
            Raster shape in 2D (heigh, width).
        shape3d:
            Raster shape in 3D (1, heigh, width),
            this is for compatibility with `mg.RasterLayer` and `rasterio`.
        array_cells:
            Array type of the `PatchCell` stored in this module.
        coords:
            Coordinate system of the raster data.
            This is useful when working with `xarray.DataArray`.
        random:
            A random proxy by calling the cells as an `ActorsList`.
    """

    def __init__(
        self,
        model: MainModel[Any, Any],
        name: Optional[str] = None,
        cell_cls: Type[PatchCell] = PatchCell,
        **kwargs: Any,
    ):
        """This method copied some of the `mesa-geo.RasterLayer`'s methods."""
        Module.__init__(self, model, name=name)
        RasterBase.__init__(self, **kwargs)
        self.cell_cls = cell_cls
        logger.info("Initializing a new Model Layer...")
        logger.info(f"Using rioxarray version: {rioxarray.__version__}")

        self._cells: List[List[PatchCell]] = []
        # obj_array = np.empty((self.height, self.width), dtype=object)
        for x in range(self.width):
            col: list[PatchCell] = []
            for y in range(self.height):
                row_idx, col_idx = self.height - y - 1, x
                cell = cell_cls(
                    layer=self, pos=(x, y), indices=(row_idx, col_idx)
                )
                # obj_array[row_idx, col_idx] = cell
                col.append(cell)
            self._cells.append(col)

        self._attributes: Set[str] = set()
        self._updated_ticks: List[int] = []

    @property
    def cells(self) -> List[List[PatchCell]]:
        """The cells stored in this layer."""
        return self._cells

    @overload
    def __getitem__(self, index: int) -> List[type[PatchCell]]:
        ...

    @overload
    def __getitem__(
        self, index: tuple[int | slice, int | slice]
    ) -> PatchCell | list[PatchCell]:
        ...

    @overload
    def __getitem__(self, index: Sequence[Coordinate]) -> list[PatchCell]:
        ...

    def __getitem__(
        self,
        index: int | Sequence[Coordinate] | tuple[int | slice, int | slice],
    ) -> PatchCell | list[PatchCell]:
        """
        Access contents from the grid.
        """

        if isinstance(index, int):
            # cells[x]
            return self.cells[index]

        if isinstance(index[0], tuple):
            # cells[(x1, y1), (x2, y2)]
            index = cast(Sequence[Coordinate], index)

            cells = []
            for pos in index:
                x1, y1 = pos
                cells.append(self.cells[x1][y1])
            return cells

        x, y = index

        if isinstance(x, int) and isinstance(y, int):
            # cells[x, y]
            x, y = cast(Coordinate, index)
            return self.cells[x][y]

        if isinstance(x, int):
            # cells[x, :]
            x = slice(x, x + 1)

        if isinstance(y, int):
            # grid[:, y]
            y = slice(y, y + 1)

        # cells[:, :]
        x, y = (cast(slice, x), cast(slice, y))
        cells = []
        for rows in self.cells[x]:
            cells.extend(iter(rows[y]))
        return cells

    def __iter__(self) -> Iterator[PatchCell]:
        """
        Create an iterator that chains the rows of the cells together
        as if it is one list
        """

        return itertools.chain.from_iterable(self.cells)

    @property
    def cell_properties(self) -> set[str]:
        """The accessible attributes of cells stored in this layer.
        All `PatchCell` methods decorated by `raster_attribute` should be appeared here.
        """
        return self.cell_cls.__attribute_properties__()

    @property
    def attributes(self) -> set[str]:
        """All accessible attributes from this layer."""
        return self._attributes | self.cell_properties

    @property
    def shape2d(self) -> Coordinate:
        """Raster shape in 2D (height, width).
        This is useful when working with 2d `numpy.array`.
        """
        return self.height, self.width

    @property
    def shape3d(self) -> Coordinate:
        """Raster shape in 3D (1, heigh, width).
        This is useful when working with `rasterio` band.
        """
        return 1, self.height, self.width

    @property
    def array_cells(self) -> np.ndarray:
        """Array type of the `PatchCell` stored in this module."""
        return np.flipud(np.array(self.cells).T)

    @property
    def coords(self) -> Coordinate:
        """Coordinate system of the raster data.
        This is useful when working with `xarray.DataArray`.
        """
        x_arr = np.linspace(
            self.total_bounds[0], self.total_bounds[2], self.width
        )
        y_arr = np.linspace(
            self.total_bounds[3], self.total_bounds[1], self.height
        )
        return {
            "y": y_arr,
            "x": x_arr,
        }

    @classmethod
    def from_resolution(
        cls,
        model: MainModel[Any, Any],
        name: Optional[str] = None,
        shape: Coordinate = (10, 10),
        crs: Optional[pyproj.CRS | str] = CRS,
        resolution: Union[int, float] = 1,
        cell_cls: type[PatchCell] = PatchCell,
    ) -> Self:
        """Create a layer from resolution.

        Parameters:
            model:
                ABSESpy Model that the new module belongs.
            name:
                Name of the new module.
                If None (by default), using lowercase of the '__class__.__name__'.
                E.g., class Module -> module.
            shape:
                Array shape (height, width) of the new module.
                For example, `shape=(3, 5)` means the new module stores 15 cells.
            crs:
                Coordinate Reference Systems.
                If passing a string object, should be able to parsed by `pyproj`.
                By default, we use CRS = "epsg:4326".
            resolution:
                Spatial Resolution when creating the coordinates.
                By default 1, it means shape (3, 5) will generate coordinates:
                {y: [0, 1, 2], x: [0, 1, 2, 3, 4]}.
                Similar, when using resolution=0.1,
                it will be {y: [.0, .1, .2], x: [.0, .1, .2, .3, .4]}.
            cell_cls:
                Class type of `PatchCell` to create.

        Returns:
            A new instance of self ("PatchModule").
        """
        height, width = shape
        total_bounds = [0, 0, width * resolution, height * resolution]
        return cls(
            model,
            name=name,
            width=width,
            height=height,
            crs=crs,
            total_bounds=total_bounds,
            cell_cls=cell_cls,
        )

    @classmethod
    def copy_layer(
        cls,
        model: MainModel[Any, Any],
        layer: Self,
        name: Optional[str] = None,
        cell_cls: Type[PatchCell] = PatchCell,
    ) -> Self:
        """Copy an existing layer to create a new layer.

        Parameters:
            model:
                ABSESpy Model that the new module belongs.
            layer:
                Another layer to copy.
                These attributes will be copied:
                including the coordinates, the crs, and the shape.
            name:
                Name of the new module.
                If None (by default), using lowercase of the '__class__.__name__'.
                E.g., class Module -> module.
            cell_cls:
                Class type of `PatchCell` to create.

        Returns:
            A new instance of self ("PatchModule").
        """
        if not isinstance(layer, PatchModule):
            raise TypeError(f"{layer} is not a valid PatchModule.")

        return cls(
            model=model,
            name=name,
            width=layer.width,
            height=layer.height,
            crs=layer.crs,
            total_bounds=layer.total_bounds,
            cell_cls=cell_cls,
        )

    @classmethod
    def from_file(
        cls,
        raster_file: str,
        cell_cls: type[PatchCell] = PatchCell,
        attr_name: str | None = None,
        model: Optional[MainModel[Any, Any]] = None,
        name: str | None = None,
    ) -> Self:
        """Create a raster layer module from a file.

        Parameters:
            raster_file:
                File path of a geo-tiff dataset.
            model:
                ABSESpy Model that the new module belongs.
            attr_name:
                Assign a attribute name to the loaded raster data.
            name:
                Name of the new module.
                If None (by default), using lowercase of the '__class__.__name__'.
                E.g., class Module -> module.
            cell_cls:
                Class type of `PatchCell` to create.

        """
        if model is None:
            raise ABSESpyError("No `model` module defined for module.")
        with rio.open(raster_file, "r") as dataset:
            values = dataset.read()
            _, height, width = values.shape
            total_bounds = [
                dataset.bounds.left,
                dataset.bounds.bottom,
                dataset.bounds.right,
                dataset.bounds.top,
            ]
        obj = cls(
            model=model,
            name=name,
            width=width,
            height=height,
            crs=dataset.crs,
            total_bounds=total_bounds,
            cell_cls=cell_cls,
        )
        obj._transform = dataset.transform
        obj.apply_raster(values, attr_name=attr_name)
        return obj

    def to_crs(self, crs, inplace=False) -> Self | None:
        super()._to_crs_check(crs)
        layer = self if inplace else copy.copy(self)

        src_crs = rio.crs.CRS.from_user_input(layer.crs)
        dst_crs = rio.crs.CRS.from_user_input(crs)
        if not layer.crs.is_exact_same(crs):
            transform, _, _ = calculate_default_transform(
                src_crs,
                dst_crs,
                self.width,
                self.height,
                *layer.total_bounds,
            )
            layer.total_bounds = [
                *transform_bounds(src_crs, dst_crs, *layer.total_bounds)
            ]
            layer.crs = crs
            layer.transform = transform

        if not inplace:
            return layer
        return None

    def _attr_or_array(
        self, data: None | str | np.ndarray | xr.DataArray
    ) -> np.ndarray:
        """Determine the incoming data type and turn it into a reasonable array."""
        if data is None:
            return np.ones(self.shape2d)
        if isinstance(data, xr.DataArray):
            data = data.to_numpy()
        if isinstance(data, np.ndarray):
            if data.shape == self.shape2d:
                return data
            raise ABSESpyError(
                f"Shape mismatch: {data.shape} [input] != {self.shape2d} [expected]."
            )
        if isinstance(data, str) and data in self.attributes:
            return self.get_raster(data)
        raise TypeError("Invalid data type or shape.")

    def dynamic_var(self, attr_name: str) -> np.ndarray:
        """Update and get dynamic variable.

        Parameters:
            attr_name:
                The dynamic variable to retrieve.

        Returns:
            2D numpy.ndarray data of the variable.
        """
        if self.time.tick in self._updated_ticks:
            return super().dynamic_var(attr_name)
        array = super().dynamic_var(attr_name)
        # 判断算出来的是一个符合形状的矩阵
        self._attr_or_array(array)
        # 将矩阵转换为三维，并更新空间数据
        array_3d = array.reshape(self.shape3d)
        self.apply_raster(array_3d, attr_name=attr_name)
        self._updated_ticks.append(self.time.tick)
        return array

    def get_rasterio(self, attr_name: str | None = None) -> rio.MemoryFile:
        """Gets the Rasterio raster layer corresponding to the attribute. Save to a temporary rasterio memory file.

        Parameters:
            attr_name:
                The attribute name for creating the rasterio file.

        Returns:
            The rasterio tmp memory file of raster.
        """
        if attr_name is None:
            data = np.ones(self.shape2d)
        else:
            data = self.get_raster(attr_name=attr_name)
        # 如果获取到的是2维，重整为3维
        if len(data.shape) != 3:
            data = data.reshape(self.shape3d)
        with rio.MemoryFile() as mem_file:
            with mem_file.open(
                driver="GTiff",
                height=data.shape[1],
                width=data.shape[2],
                count=data.shape[0],  # number of bands
                dtype=str(data.dtype),
                crs=self.crs,
                transform=self.transform,
            ) as dataset:
                dataset.write(data)
            # Open the dataset again for reading and return
            return mem_file.open()

    def get_xarray(self, attr_name: Optional[str] = None) -> xr.DataArray:
        """Get the xarray raster layer with spatial coordinates.

        Parameters:
            attr_name:
                The attribute to retrieve. If None (by default), return all available attributes (3D DataArray). Otherwise, 2D DataArray of the chosen attribute.

        Returns:
            Xarray.DataArray data with spatial coordinates of the chosen attribute.
        """
        data = self.get_raster(attr_name=attr_name)
        if attr_name:
            name = attr_name
            data = data.reshape(self.shape2d)
            coords = self.coords
        else:
            coords = {"variable": list(self.attributes)}
            coords |= self.coords
            name = self.name
        return xr.DataArray(
            data=data,
            name=name,
            coords=coords,
        ).rio.write_crs(self.crs)

    @property
    def random(self) -> ListRandom:
        """Randomly"""
        return self.select().random

    def _select_by_geometry(
        self,
        geometry: Geometry,
        refer_layer: Optional[str] = None,
        **kwargs: Dict[str, Any],
    ) -> np.ndarray:
        """Gets all the cells that intersect the given geometry.

        Parameters:
            geometry:
                Shapely Geometry to search intersected cells.
            refer_layer:
                The attribute name to refer when filtering cells.
            **kwargs:
                Args pass to the function `rasterio.mask.mask`. It influence how to build the mask for filtering cells. Please refer [this doc](https://rasterio.readthedocs.io/en/latest/api/rasterio.mask.html) for details.

        Raises:
            ABSESpyError:
                If no available attribute exists, or the assigned refer layer is not available in the attributes.

        Returns:
            A list of PatchCell.
        """
        if refer_layer is not None and refer_layer not in self.attributes:
            raise ABSESpyError(
                f"The refer layer {refer_layer} is not available in the attributes"
            )
        data = self.get_rasterio(attr_name=refer_layer)
        out_image, _ = mask.mask(data, [geometry], **kwargs)
        return out_image.reshape(self.shape2d)

    def select(
        self,
        where: Optional[str | np.ndarray | xr.DataArray | Geometry] = None,
    ) -> ActorsList[PatchCell]:
        """Select cells from this layer.

        Parameters:
            where:
                The condition to select cells.
                If None (by default), select all cells.
                If a string, select cells by the attribute name.
                If a numpy.ndarray, select cells by the mask array.
                If a Shapely Geometry, select cells by the intersection with the geometry.

        Raises:
            TypeError:
                If the input type is not supported.

        Returns:
            An `ActorsList` with all selected cells stored.
        """
        if isinstance(where, Geometry):
            mask_ = self._select_by_geometry(geometry=where)
        elif (
            isinstance(where, (np.ndarray, str, xr.DataArray)) or where is None
        ):
            mask_ = self._attr_or_array(where).reshape(self.shape2d)
        else:
            raise TypeError(
                f"{type(where)} is not supported for selecting cells."
            )
        mask_ = np.nan_to_num(mask_, nan=0.0).astype(bool)
        return ActorsList(self.model, self.array_cells[mask_])

    def apply(
        self, ufunc: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> np.ndarray:
        """Apply a function to array cells.

        Parameters:
            ufunc:
                A function to apply.
            *args:
                Positional arguments to pass to the function.
            **kwargs:
                Keyword arguments to pass to the function.

        Returns:
            The result of the function applied to the array cells.
        """
        func = functools.partial(ufunc, *args, **kwargs)
        return np.vectorize(func)(self.array_cells, *args, **kwargs)

    def sel(self, where) -> ActorsList[PatchCell]:
        """Select cells from this layer.

        Parameters:
            where:
                The condition to select cells.
                If None (by default), select all cells.
                If a string, select cells by the attribute name.
                If a numpy.ndarray, select cells by the mask array.
                If a Shapely Geometry, select cells by the intersection with the geometry.

        Returns:
            An `ActorsList` with all selected cells stored.
        """
        return self.select(where)

    def coord_iter(self) -> Iterator[tuple[PatchCell, int, int]]:
        """
        An iterator that returns coordinates as well as cell contents.
        """

        for row in range(self.width):
            for col in range(self.height):
                yield self.cells[row][col], row, col  # cell, x, y

    def apply_raster(
        self, data: np.ndarray, attr_name: str | None = None
    ) -> None:
        """
        Apply raster data to the cells.

        :param np.ndarray data: 2D numpy array with shape (1, height, width).
        :param str | None attr_name: Name of the attribute to be added to the cells.
            If None, a random name will be generated. Default is None.
        :raises ValueError: If the shape of the data is not (1, height, width).
        """

        if data.shape != (1, self.height, self.width):
            raise ValueError(
                f"Data shape does not match raster shape. "
                f"Expected {(1, self.height, self.width)}, received {data.shape}."
            )
        if attr_name is None:
            attr_name = f"attribute_{len(self.cell_cls.__dict__)}"
        self._attributes.add(attr_name)
        for x in range(self.width):
            for y in range(self.height):
                setattr(
                    self.cells[x][y],
                    attr_name,
                    data[0, self.height - y - 1, x],
                )

    def get_raster(self, attr_name: Optional[str] = None) -> np.ndarray:
        """Obtaining the Raster layer by attribute.

        Parameters:
            attr_name:
                The attribute to retrieve. Update it if it is a dynamic variable. If None (by default), retrieve all attributes as a 3D array.

        Returns:
            A 3D array of attribute.
        """
        if attr_name in self._dynamic_variables:
            return self.dynamic_var(attr_name=attr_name).reshape(self.shape3d)
        if attr_name is not None and attr_name not in self.attributes:
            raise ValueError(
                f"Attribute {attr_name} does not exist. "
                f"Choose from {self.attributes}, or set `attr_name` to `None` to retrieve all."
            )
        if attr_name is None:
            num_bands = len(self.attributes)
            attr_names = self.attributes
        else:
            num_bands = 1
            attr_names = {attr_name}
        data = np.empty((num_bands, self.height, self.width))
        for ind, name in enumerate(attr_names):
            for x in range(self.width):
                for y in range(self.height):
                    data[ind, self.height - y - 1, x] = getattr(
                        self.cells[x][y], name
                    )
        return data

    def iter_neighborhood(
        self,
        pos: Coordinate,
        moore: bool,
        include_center: bool = False,
        radius: int = 1,
    ) -> Iterator[Coordinate]:
        """
        Return an iterator over cell coordinates that are in the
        neighborhood of a certain point.

        :param Coordinate pos: Coordinate tuple for the neighborhood to get.
        :param bool moore: Whether to use Moore neighborhood or not. If True,
            return Moore neighborhood (including diagonals). If False, return
            Von Neumann neighborhood (exclude diagonals).
        :param bool include_center: If True, return the (x, y) cell as well.
            Otherwise, return surrounding cells only. Default is False.
        :param int radius: Radius, in cells, of the neighborhood. Default is 1.
        :return: An iterator over cell coordinates that are in the neighborhood.
            For example with radius 1, it will return list with number of elements
            equals at most 9 (8) if Moore, 5 (4) if Von Neumann (if not including
            the center).
        :rtype: Iterator[Coordinate]
        """

        yield from self.get_neighborhood(pos, moore, include_center, radius)

    def iter_neighbors(
        self,
        pos: Coordinate,
        moore: bool,
        include_center: bool = False,
        radius: int = 1,
    ) -> Iterator[PatchCell]:
        """
        Return an iterator over neighbors to a certain point.

        :param Coordinate pos: Coordinate tuple for the neighborhood to get.
        :param bool moore: Whether to use Moore neighborhood or not. If True,
            return Moore neighborhood (including diagonals). If False, return
            Von Neumann neighborhood (exclude diagonals).
        :param bool include_center: If True, return the (x, y) cell as well.
            Otherwise, return surrounding cells only. Default is False.
        :param int radius: Radius, in cells, of the neighborhood. Default is 1.
        :return: An iterator of cells that are in the neighborhood; at most 9 (8)
            if Moore, 5 (4) if Von Neumann (if not including the center).
        :rtype: Iterator[Cell]
        """

        neighborhood = self.get_neighborhood(
            pos, moore, include_center, radius
        )
        return self.iter_cell_list_contents(neighborhood)

    @accept_tuple_argument
    def iter_cell_list_contents(
        self, cell_list: Iterable[Coordinate]
    ) -> Iterator[PatchCell]:
        """
        Returns an iterator of the contents of the cells
        identified in cell_list.

        :param Iterable[Coordinate] cell_list: Array-like of (x, y) tuples,
            or single tuple.
        :return: An iterator of the contents of the cells identified in cell_list.
        :rtype: Iterator[Cell]
        """

        # Note: filter(None, iterator) filters away an element of iterator that
        # is falsy. Hence, iter_cell_list_contents returns only non-empty
        # contents.
        return filter(None, (self.cells[x][y] for x, y in cell_list))

    @accept_tuple_argument
    def get_cell_list_contents(
        self, cell_list: Iterable[Coordinate]
    ) -> list[PatchCell]:
        """
        Returns a list of the contents of the cells
        identified in cell_list.

        Note: this method returns a list of cells.

        :param Iterable[Coordinate] cell_list: Array-like of (x, y) tuples,
            or single tuple.
        :return: A list of the contents of the cells identified in cell_list.
        :rtype: List[Cell]
        """

        return list(self.iter_cell_list_contents(cell_list))

    @functools.lru_cache(maxsize=1000)
    def get_neighborhood(
        self,
        pos: Coordinate,
        moore: bool,
        include_center: bool = False,
        radius: int = 1,
    ) -> list[Coordinate]:
        coordinates: set[Coordinate] = set()

        x, y = pos
        for dy, dx in itertools.product(
            range(-radius, radius + 1), range(-radius, radius + 1)
        ):
            if dx == 0 and dy == 0 and not include_center:
                continue
            # Skip coordinates that are outside manhattan distance
            if not moore and abs(dx) + abs(dy) > radius:
                continue

            coord = (x + dx, y + dy)

            if self.out_of_bounds(coord):
                continue
            coordinates.add(coord)
        return sorted(coordinates)

    def get_neighboring_cells(
        self,
        pos: Coordinate,
        moore: bool,
        include_center: bool = False,
        radius: int = 1,
    ) -> list[PatchCell]:
        neighboring_cell_idx = self.get_neighborhood(
            pos, moore, include_center, radius
        )
        return [self.cells[idx[0]][idx[1]] for idx in neighboring_cell_idx]

    def to_file(
        self,
        raster_file: str,
        attr_name: str | None = None,
        driver: str = "GTiff",
    ) -> None:
        """
        Writes a raster layer to a file.

        :param str raster_file: The path to the raster file to write to.
        :param str | None attr_name: The name of the attribute to write to the raster.
            If None, all attributes are written. Default is None.
        :param str driver: The GDAL driver to use for writing the raster file.
            Default is 'GTiff'. See GDAL docs at https://gdal.org/drivers/raster/index.html.
        """

        data = self.get_raster(attr_name)
        with rio.open(
            raster_file,
            "w",
            driver=driver,
            width=self.width,
            height=self.height,
            count=data.shape[0],
            dtype=data.dtype,
            crs=self.crs,
            transform=self.transform,
        ) as dataset:
            dataset.write(data)


class BaseNature(mg.GeoSpace, CompositeModule):
    """The Base Nature Module.
    Note:
        Look at [this tutorial](../tutorial/beginner/organize_model_structure.ipynb) to understand the model structure.
        This is NOT a raster layer, but can be seen as a container of different raster layers.
        Users can create new raster layer (i.e., `PatchModule`) by `create_module` method.
        By default, an initialized ABSESpy model will init an instance of this `BaseNature` as `nature` module.

    Attributes:
        major_layer:
            The major layer of nature module. By default, it's the first layer that user created.
        total_bounds:
            The spatial scope of the model's concern. By default, uses the major layer of this model.
    """

    def __init__(
        self, model: MainModel[Any, Any], name: str = "nature"
    ) -> None:
        CompositeModule.__init__(self, model, name=name)
        crs = self.params.get("crs", CRS)
        mg.GeoSpace.__init__(self, crs=crs)
        self._major_layer: Optional[PatchModule] = None

        logger.info("Initializing a new Base Nature module...")

    @property
    def major_layer(self) -> PatchModule | None:
        """The major layer of nature module.
        By default, it's the first created layer.
        """
        return self._major_layer

    @major_layer.setter
    def major_layer(self, layer: PatchModule) -> None:
        if not isinstance(layer, PatchModule):
            raise TypeError(f"{layer} is not PatchModule.")
        self._major_layer = layer
        self.crs = layer.crs

    @property
    def total_bounds(self) -> np.ndarray | None:
        """Total bounds. The spatial scope of the model's concern.
        If None (by default), uses the major layer of this model.
        Usually, the major layer is the first layer sub-module you created.
        """
        if self._total_bounds is not None:
            return self._total_bounds
        if hasattr(self, "major_layer") and self.major_layer:
            return self.major_layer.total_bounds
        return None

    def create_module(
        self,
        module_class: Optional[Type[Module]] = None,
        how: Optional[str] = None,
        **kwargs: Any,
    ) -> PatchModule:
        """Creates a submodule of the raster layer.

        Parameters:
            module_class:
                The custom module class.
            how:
                Class method to call when creating the new sub-module (raster layer).
                So far, there are three options:
                    `from_resolution`: by selecting shape and resolution.
                    `from_file`: by input of a geo-tiff dataset.
                    `copy_layer`: by copying shape, resolution, bounds, crs, and coordinates of an existing submodule.
                if None (by default), just simply create a sub-module without any custom methods (i.e., use the base class `PatchModule`).
            **kwargs:
                Any other arg passed to the creation method.
                See corresponding method of your how option from `PatchModule` class methods.

        Returns:
            the created new module.
        """
        if module_class is None:
            module_class = PatchModule
        assert issubclass(
            module_class, PatchModule
        ), "Must be a `PatchModule`."
        module = cast(
            PatchModule, super().create_module(module_class, how, **kwargs)
        )
        # 如果是第一个创建的模块,则将其作为主要的图层
        if not self.layers:
            self.major_layer = module
        self.add_layer(module)
        return module
