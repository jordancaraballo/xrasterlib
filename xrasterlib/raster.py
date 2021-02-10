import os  # system library
import logging  # logging messages
import operator  # operator library
import numpy as np  # array manipulation library
import xarray as xr  # array manipulation library, rasterio built-in
import rasterio as rio  # geospatial library
from scipy.ndimage import median_filter  # scipy includes median filter
import rasterio.features as riofeat  # rasterio features include sieve filter

try:
    from cupyx.scipy.ndimage import median_filter as cp_medfilter
    import cupy as cp
    HAS_GPU = True
except ImportError:
    HAS_GPU = False

__author__ = "Jordan A Caraballo-Vega, Science Data Processing Branch"
__email__ = "jordan.a.caraballo-vega@nasa.gov"
__status__ = "Production"

# -------------------------------------------------------------------------------
# class Raster
#
# This class represents, reads and manipulates raster. Currently support TIF
# formatted imagery.
# -------------------------------------------------------------------------------


class Raster:

    # ---------------------------------------------------------------------------
    # __init__
    # ---------------------------------------------------------------------------
    def __init__(self, filename: str = None, bands: list = None,
                 chunks_band: int = 1, chunks_x: int = 2048,
                 chunks_y: int = 2048, logger=None):
        """
        Default Raster initializer
        ----------
        Parameters
        ----------
        :params filename: raster filename to read from
        :params bands: list of bands to append to object - Red Green Blue etc.
        :params chunks_band: integer to map object to memory, z
        :params chunks_x: integer to map object to memory, x
        :params chunks_y: integer to map object to memory, y
        :params logger: log file
        ----------
        Attributes
        ----------
        self.logger: str - filename to store logs
        self.has_gpu: bool - global value to determine if GPU is available
        self.data_chunks: dict - dictionary to feed xarray rasterio object
        self.data: xarray, rasterio type - raster data stored in xarray type
        self.bands: list of str - band names - Red Green Blue etc.
        self.nodataval: int - default no-data value used in the
        ----------
        Example
            Raster(filename, bands)
        ----------
        """

        self.logger = logger

        self.has_gpu = HAS_GPU

        if filename is not None:  # if filename is provided, read into xarray

            if not os.path.isfile(filename):
                raise RuntimeError('{} does not exist'.format(filename))

            self.data_chunks = {
                'band': chunks_band,
                'x': chunks_x,
                'y': chunks_y
            }

            self.data = xr.open_rasterio(filename, chunks=self.data_chunks)

            if bands is None:
                raise RuntimeError('Must specify band names.')

            self.bands = bands

            self.nodataval = self.data.attrs['nodatavals']

    # ---------------------------------------------------------------------------
    # methods
    # ---------------------------------------------------------------------------

    # ---------------------------------------------------------------------------
    # input
    # ---------------------------------------------------------------------------
    def readraster(self, filename: str, bands: list, chunks_band: int = 1,
                   chunks_x: int = 2048, chunks_y: int = 2048):
        """
        Read raster and append data to existing Raster object
        :params filename: raster filename to read from
        :params bands: list of bands to append to object - Red Green Blue etc.
        :params chunks_band: integer to map object to memory, z
        :params chunks_x: integer to map object to memory, x
        :params chunks_y: integer to map object to memory, y
        ----------
        Example
            raster.readraster(filename, bands)
        ----------
        """
        self.data_chunks = {'band': chunks_band, 'x': chunks_x, 'y': chunks_y}
        self.data = xr.open_rasterio(filename, chunks=self.data_chunks)
        self.bands = bands
        self.nodataval = self.data.attrs['nodatavals']

    # ---------------------------------------------------------------------------
    # preprocessing
    # ---------------------------------------------------------------------------
    def preprocess(self, op: str = '>', boundary: int = 0, subs: int = 0):
        """
        Remove anomalous values from self.data
        :params op: str with operator, currently <, and >
        :params boundary: boundary for classifying as anomalous
        :params subs: value to replace withint, float
        ----------
        Example
            raster.preprocess(op='>', boundary=0, replace=0) := get all values
            that satisfy the condition self.data > boundary (above 0).
        ----------
        """
        ops = {'<': operator.lt, '>': operator.gt}
        self.data = self.data.where(ops[op](self.data, boundary), other=subs)

    def addindices(self, indices: list, factor: float = 1.0):
        """
        Add multiple indices to existing Raster object self.data.
        :params indices: list of indices functions
        :params factor: atmospheric factor for indices calculation
        ----------
        Example
            raster.addindices([indices.fdi, indices.si], factor=10000.0)
        ----------
        """
        nbands = len(self.bands)  # get initial number of bands
        for indices_function in indices:  # iterate over each new band
            nbands += 1  # counter for number of bands
            # calculate band (indices)
            band, bandid = \
                indices_function(self.data, bands=self.bands, factor=factor)
            self.bands.append(bandid)  # append new band id to list of bands
            band.coords['band'] = [nbands]  # add band indices to raster
            self.data = xr.concat([self.data, band], dim='band')  # concat band

        # update raster metadata, xarray attributes
        self.data.attrs['scales'] = [self.data.attrs['scales'][0]] * nbands
        self.data.attrs['offsets'] = [self.data.attrs['offsets'][0]] * nbands

    def dropindices(self, dropindices):
        """
        Add multiple indices to existing Raster object self.data.
        :params dropindices: list of indices functions
        ----------
        Example
            raster.dropindices(band_ids)
        ----------
        """
        assert all(band in self.bands for band in dropindices), \
               "Specified band not in raster."
        dropind = [self.bands.index(ind_id)+1 for ind_id in dropindices]
        self.data = self.data.drop(dim="band", labels=dropind, drop=True)
        self.bands = [band for band in self.bands if band not in dropindices]

    # ---------------------------------------------------------------------------
    # post processing
    # ---------------------------------------------------------------------------
    def sieve(self, prediction: np.array, out: np.array,
              size: int = 350, mask: str = None, connectivity: int = 8):
        """
        :param prediction: numpy array with prediction output
        :param out: numpy array with prediction output to store on
        :param size: size of sieve
        :param mask: file to save at
        :param connectivity: size of sieve
        :return: None, numpy array
        ----------
        Example
            raster.sieve(raster.prediction, raster.prediction, size=sieve_sz)
        ----------
        """
        riofeat.sieve(prediction, size, out, mask, connectivity)

    def median(self, prediction: np.array, ksize: int = 20) -> np.array:
        """
        Apply median filter for postprocessing
        :param prediction: numpy array with prediction output
        :param ksize: size of kernel for median filter
        :return: numpy array
        ----------
        Example
            raster.median(raster.prediction, ksize=args.median_sz)
        ----------
        """
        if self.has_gpu:  # method for GPU
            with cp.cuda.Device(1):
                prediction = cp_medfilter(cp.asarray(prediction), size=ksize)
            return cp.asnumpy(prediction)
        else:  # method for CPU
            return median_filter(prediction, size=ksize)

    # ---------------------------------------------------------------------------
    # output
    # ---------------------------------------------------------------------------

    def toraster(self, rast: str, prediction: np.array,
                 dtype: str = 'int16', output: str = 'rfmask.tif'):
        """
        Save tif file from numpy to disk.
        :param rast: raster name to get metadata from
        :param prediction: numpy array with prediction output
        :param dtype type to store mask on
        :param output: raster name to save on
        :return: None, tif file saved to disk
        ----------
        Example
            raster.toraster(filename, raster_obj.prediction, outname)
        ----------
        """
        # get meta features from raster
        with rio.open(rast) as src:
            meta = src.profile
            nodatavals = src.read_masks(1).astype(dtype)
        logging.info(meta)

        nodatavals[nodatavals == 0] = self.nodataval[0]
        prediction[nodatavals == self.nodataval[0]] = \
            nodatavals[nodatavals == self.nodataval[0]]

        out_meta = meta  # modify profile based on numpy array
        out_meta['count'] = 1  # output is single band
        out_meta['dtype'] = dtype  # data type is float64

        # write to a raster
        with rio.open(output, 'w', **out_meta) as dst:
            dst.write(prediction, 1)
        logging.info(f'Prediction saved at {output}')

# -------------------------------------------------------------------------------
# class Raster Unit Tests
# -------------------------------------------------------------------------------


if __name__ == "__main__":

    # Running Unit Tests
    print("Unit tests located under xrasterlib/tests/raster.py")