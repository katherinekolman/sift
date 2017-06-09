#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
metadatabase.py
===============

PURPOSE
SQLAlchemy database tables of metadata used by Workspace to manage its local cache.


OVERVIEW

Resource : a file containing products, somewhere in the filesystem, or a resource on a remote system we can access (openDAP etc)
 |_ Product* : product stored in a resource
     |_ Content* : workspace cache content corresponding to a product, may be one of many available views (e.g. projections)
     |   |_ ContentKeyValue* : additional information on content
     |_ ProductKeyValue* : additional information on product
     |_ SymbolKeyValue* : if product is derived from other products, symbol table for that expression is in this kv table

A typical baseline product will have two content: and overview (lod==0) and a native resolution (lod>0)


REQUIRES
SQLAlchemy with SQLite

:author: R.K.Garcia <rayg@ssec.wisc.edu>
:copyright: 2016 by University of Wisconsin Regents, see AUTHORS for more details
:license: GPLv3, see LICENSE for more details
"""
__author__ = 'rayg'
__docformat__ = 'reStructuredText'

import os, sys
import logging, unittest, argparse
from datetime import datetime, timedelta
from sift.common import INFO
from functools import reduce
from uuid import UUID
from collections import ChainMap, MutableMapping
from typing import Mapping

from sqlalchemy import Table, Column, Integer, String, UnicodeText, Unicode, ForeignKey, DateTime, Interval, PickleType, Float, create_engine
from sqlalchemy.orm import Session, relationship, sessionmaker, backref
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm.collections import attribute_mapped_collection
from sqlalchemy.ext.associationproxy import association_proxy

LOG = logging.getLogger(__name__)

#
# ref   http://docs.sqlalchemy.org/en/latest/_modules/examples/vertical/dictlike.html
#

# class ProxiedDictMixin(object):
#     """Adds obj[key] access to a mapped class.
#
#     This class basically proxies dictionary access to an attribute
#     called ``_proxied``.  The class which inherits this class
#     should have an attribute called ``_proxied`` which points to a dictionary.
#
#     """
#
#     def __len__(self):
#         return len(self._proxied)
#
#     def __iter__(self):
#         return iter(self._proxied)
#
#     def __getitem__(self, key):
#         # if key in INFO:
#         #     v = getattr(self, str(key), INFO)
#         #     if v is not INFO:
#         #         return v
#         return self._proxied[key]
#
#     def __contains__(self, key):
#         return key in self._proxied
#
#     def __setitem__(self, key, value):
#         self._proxied[key] = value
#
#     def __delitem__(self, key):
#         del self._proxied[key]




# =================
# Database Entities

Base = declarative_base()

# resources can have multiple products in them
# products may require multiple resourcse (e.g. separate GEO; tiled imagery)
ProductsFromResources = Table('product_resource_assoc_v0', Base.metadata,
                              Column('product_id', Integer, ForeignKey('products_v0.id')),
                              Column('resource_id', Integer, ForeignKey('resources_v0.id')))



class Resource(Base):
    """
    held metadata regarding a file that we can access and import data into the workspace from
    resources are external to the workspace, but the workspace can keep track of them in its database
    """
    __tablename__ = 'resources_v0'
    # identity information
    id = Column(Integer, primary_key=True)

    # primary handler
    format = Column(PickleType)  # classname, class or callable which can pull this data into workspace from storage

    # {scheme}://{path}/{name}?{query}, default is just an absolute path in filesystem
    scheme = Column(Unicode, nullable=True)  # uri scheme for the content (the part left of ://), assume file:// by default
    path = Column(Unicode)  # '/' separated real path
    query = Column(Unicode, nullable=True)  # query portion of a URI or URL, e.g. 'interval=1m&stride=2'

    mtime = Column(DateTime)  # last observed mtime of the file, for change checking
    atime = Column(DateTime)  # last time this file was accessed by application

    product = relationship("Product", secondary=ProductsFromResources, backref="resource")

    @property
    def uri(self):
        return self.path if (not self.scheme or self.scheme=='file') else "{}://{}/{}{}".format(self.scheme, self.path, self.name, '' if not self.query else '?' + self.query)

    def touch(self, when=None):
        self.atime = datetime.utcnow() if not when else when

    def exists(self):
        if self.scheme not in {None, 'file'}:
            return True  # FUTURE: alternate tests for still-exists-ness
        return os.path.exists(self.path)


class ChainRecordWithDict(MutableMapping):
    """
    allow Product database entries and key-value table to act as a coherent dictionary
    """
    def __init__(self, obj, field_keys, more):
        self._obj, self._field_keys, self._more = obj, field_keys, more

    def keys(self):
        return set(self._more.keys()) + set(self._field_keys.keys)

    def items(self):
        for k in self.keys():
            yield k, self[k]

    def values(self):
        for k in self.keys():
            yield self[k]

    def __len__(self):
        return len(self.keys())

    def __iter__(self):
        yield from self.items()

    def __contains__(self, item):
        return item in self.keys()

    def __getitem__(self, key):
        if key in self._field_keys:
            return self._obj.__dict__[key]
        return self._more[key]

    def __setitem__(self, key, value):
        if key in self._field_keys:
            self._obj.__dict__[key] = value
            # setattr(self._obj, key, value)
        else:
            self._more[key] = value

    def __delitem__(self, key):
        if key in self._field_keys:
            raise KeyError('cannot remove key {}'.format(key))
        del self._more[key]



class Product(Base):
    """
    Primary entity being tracked in metadatabase
    One or more StoredProduct are held in a single File
    A StoredProduct has zero or more Content representations, potentially at different projections
    A StoredProduct has zero or more ProductKeyValue pairs with additional metadata
    A File's format allows data to be imported to the workspace
    A StoredProduct's kind determines how its cached data is transformed to different representations for display
    additional information is stored in a key-value table addressable as product[key:str]
    """
    __tablename__ = 'products_v0'

    # identity information
    id = Column(Integer, primary_key=True)
    resource_id = Column(Integer, ForeignKey(Resource.id))
    # relationship: .resource
    uuid_str = Column(String, nullable=False, unique=True)  # UUID representing this data in SIFT, or None if not in cache

    @property
    def uuid(self):
        return UUID(self.uuid_str)

    @uuid.setter
    def uuid(self, uu):
        self.uuid_str = str(uu)

    # primary handler
    # kind = Column(PickleType)  # class or callable which can perform transformations on this data in workspace
    atime = Column(DateTime)  # last time this file was accessed by application

    # cached metadata provided by the file format handler
    name = Column(String)  # product identifier eg "B01", "B02"  # resource + shortname should be sufficient to identify the data

    # platform = Column(String)  # platform or satellite name e.g. "GOES-16", "Himawari-8"; should match PLATFORM enum
    # standard_name = Column(String, nullable=True)
    #
    # times
    # display_time = Column(DateTime)  # normalized instantaneous scheduled observation time e.g. 20170122T2310
    obs_time = Column(DateTime)  # actual observation time start
    obs_duration = Column(Interval)  # duration of the observation

    # native resolution information - see Content for projection details at different LODs
    # resolution = Column(Integer, nullable=True)  # meters max resolution, e.g. 500, 1000, 2000, 4000

    # descriptive - move these to INFO keys
    # units = Column(Unicode, nullable=True)  # udunits compliant units, e.g. 'K'
    # label = Column(Unicode, nullable=True)  # "AHI Refl B11"
    # description = Column(UnicodeText, nullable=True)

    # link to workspace cache files representing this data, not lod=0 is overview
    content = relationship("Content", backref=backref("product", cascade="all"), order_by=lambda: Content.lod)

    # link to key-value further information
    # this provides dictionary style access to key-value pairs
    _key_values = relationship("ProductKeyValue", collection_class=attribute_mapped_collection('key'))
    _kwinfo = association_proxy("_key_values", "value",
                                 creator=lambda key, value: ProductKeyValue(key=key, value=value))

    # derived / algebraic layers have a symbol table and an expression
    # typically Content objects for algebraic layers cache calculation output
    symbol = relationship("SymbolKeyValue", backref=backref("product", cascade="all"))
    expression = Column(Unicode, nullable=True)

    _info = None  # database fields and key-value dictionary merged as one transparent mapping

    def __init__(self, *args, **kwargs):
        super(Product, self).__init__(*args, **kwargs)
        self._info = ChainRecordWithDict(self, self.INFO_TO_FIELD, self._kwinfo)

    def __repr__(self):
        return "<Product '{}' @ {}~{}>".format(self.name, self.obs_time, self.obs_time + self.obs_duration)

    @property
    def info(self):
        """
        :return: mapping merging INFO-compatible database fields with key-value dictionary access pattern
        """
        return self._info

    @property
    def proj4(self):
        nat = self.content[-1] if len(self.content) else None
        return nat.proj4 if nat else None

    @property
    def cell_height(self):
        nat = self.content[-1] if len(self.content) else None
        return nat.cell_height if nat else None

    @property
    def cell_width(self):
        nat = self.content[-1] if len(self.content) else None
        return nat.cell_width if nat else None

    @property
    def origin_x(self):
        nat = self.content[-1] if len(self.content) else None
        return nat.origin_x if nat else None

    @property
    def origin_y(self):
        nat = self.content[-1] if len(self.content) else None
        return nat.origin_y if nat else None

    @property
    def path(self):
        if len(self.resource) > 1:
            LOG.warning('Product {} has more than one resource, path is ambiguous'.format(self.name))
        return self.resource[0].path if len(self.resource)==1 else None

    def can_be_activated_without_importing(self):
        return len(self.content)>0

    INFO_TO_FIELD = {
        INFO.SHORT_NAME: 'name',
        INFO.PATHNAME: 'path',
        INFO.UUID: 'uuid',
        INFO.PROJ: 'proj4',
        INFO.OBS_TIME: 'obs_time',
        INFO.OBS_DURATION: 'obs_duration',
        INFO.CELL_WIDTH: 'cell_width',
        INFO.CELL_HEIGHT: 'cell_height',
        INFO.ORIGIN_X: 'origin_x',
        INFO.ORIGIN_Y: 'origin_y'
    }

    def touch(self, when=None):
        self.atime = when = when or datetime.utcnow()
        [x.touch(when) for x in self.resource]

class ProductKeyValue(Base):
    """
    key-value pairs associated with a product
    """
    __tablename__ = 'product_key_values_v0'
    product_id = Column(ForeignKey(Product.id), primary_key=True)
    key = Column(String, primary_key=True)
    # relationship: .product
    value = Column(PickleType)

class SymbolKeyValue(Base):
    """
    derived layers have a symbol table which becomes namespace used by expression
    """
    __tablename__ = 'algebraic_symbol_key_values_v0'
    product_id = Column(ForeignKey(Product.id), primary_key=True)
    key = Column(Unicode, primary_key=True)
    # relationship: .product
    value = Column(PickleType, nullable=True)  # UUID object typically

class Content(Base):
    """
    represent flattened product data files in cache (i.e. cache content)
    typically memory-map ready data (np.memmap)
    basic correspondence to projection/geolocation information may accompany
    images will typically have rows>0 cols>0 levels=None (implied levels=1)
    profiles may have rows>0 cols=None (implied cols=1) levels>0
    a given product may have several Content for different projections
    additional information is stored in a key-value table addressable as content[key:str]
    """
    # _array = None  # when attached, this is a np.memmap

    __tablename__ = 'contents_v0'
    id = Column(Integer, primary_key=True)
    product_id = Column(Integer, ForeignKey(Product.id))

    # handle overview versus detailed data
    lod = Column(Integer)  # power of 2 level of detail; 0 for coarse-resolution overview
    LOD_OVERVIEW = 0

    resolution = Column(Integer)  # maximum resolution in meters for this representation of the dataset

    # time accounting, used to check if data needs to be re-imported to workspace, or whether data is LRU and can be removed from a crowded workspace
    mtime = Column(DateTime)  # last observed mtime of the original source of this data, for change checking
    atime = Column(DateTime)  # last time this product was accessed by application

    # actual data content
    # NaNs are used to signify missing data; NaNs can include integer category fields in significand; please ref IEEE 754
    path = Column(String, unique=True)  # relative to workspace, binary array of data
    rows, cols, levels = Column(Integer), Column(Integer, nullable=True), Column(Integer, nullable=True)
    dtype = Column(String, nullable=True)  # default float32; can be int16 in the future for scaled integer images for instance; should be a numpy type name
    # coeffs = Column(String, nullable=True)  # json for numpy array with polynomial coefficients for transforming native data to natural units (e.g. for scaled integers), c[0] + c[1]*x + c[2]*x**2 ...
    # values = Column(String, nullable=True)  # json for optional dict {int:string} lookup table for NaN flag fields (when dtype is float32 or float64) or integer values (when dtype is an int8/16/32/64)

    # projection information for this representation of the data
    proj4 = Column(String, nullable=True)  # proj4 projection string for the data in this array, if one exists; else assume y=lat/x=lon
    cell_width, cell_height, origin_x, origin_y = Column(Float, nullable=True), Column(Float, nullable=True), Column(Float, nullable=True), Column(Float, nullable=True)

    # sparsity and coverage, int8 arrays if needed to show incremental availability of the data
    # dimensionality is always a reduction factor of rows/cols/levels
    # coverage is stretched across the data array
    #   e.g. for loading data sectioned or blocked across multiple files
    # sparsity is broadcast over the data array
    #   e.g. for incrementally loading sparse data into a dense array
    # a zero value indicates data is not available, nonzero signifies availability
    coverage_rows, coverage_cols, coverage_levels = Column(Integer, nullable=True), Column(Integer, nullable=True), Column(Integer, nullable=True)
    coverage_path = Column(String, nullable=True)
    sparsity_rows, sparsity_cols, sparsity_levels = Column(Integer, nullable=True), Column(Integer, nullable=True), Column(Integer, nullable=True)
    sparsity_path = Column(String, nullable=True)

    # navigation information, if required
    xyz_dtype = Column(String, nullable=True)  # dtype of x,y,z arrays, default float32
    y_path = Column(String, nullable=True)  # if needed, y location cache path relative to workspace
    x_path = Column(String, nullable=True)  # if needed, x location cache path relative to workspace
    z_path = Column(String, nullable=True)  # if needed, z location cache path relative to workspace

    # link to key-value further information; primarily a hedge in case specific information has to be squirreled away for later consideration for main content table
    # this provides dictionary style access to key-value pairs
    _key_values = relationship("ContentKeyValue", collection_class=attribute_mapped_collection('key'))
    _kwinfo = association_proxy("_key_values", "value",
                                 creator=lambda key, value: ContentKeyValue(key=key, value=value))

    INFO_TO_FIELD = {
        INFO.CELL_HEIGHT: 'cell_height',
        INFO.CELL_WIDTH: 'cell_width',
        INFO.PROJ: 'proj4',
        INFO.SHORT_NAME: 'name',
        INFO.PATHNAME: 'path'
    }

    _info = None  # database fields and key-value dictionary merged as one transparent mapping

    def __init__(self, *args, **kwargs):
        super(Product, self).__init__(*args, **kwargs)
        self._info = ChainRecordWithDict(self, self.INFO_TO_FIELD, self._kwinfo)

    @property
    def info(self):
        """
        :return: mapping merging INFO-compatible database fields with key-value dictionary access pattern
        """
        return self._info

    @property
    def name(self):
        return self.product.name

    @property
    def uuid(self):
        return self.product.uuid

    @property
    def is_overview(self):
        return self.lod==self.LOD_OVERVIEW

    def __str__(self):
        product = "%s:%s.%s" % (self.product.source.name or '?', self.product.platform or '?', self.product.identifier or '?')
        isoverview = ' overview' if self.is_overview else ''
        dtype = self.dtype or 'float32'
        xyzcs = ' '.join(
            q for (q,p) in zip('XYZCS', (self.x_path, self.y_path, self.z_path, self.coverage_path, self.sparsity_path)) if p
        )
        return "<{uuid} product {product} content{isoverview} with path={path} dtype={dtype} {xyzcs}>".format(
            uuid=self.uuid, product=product, isoverview=isoverview, path=self.path, dtype=dtype, xyzcs=xyzcs)

    def touch(self, when=None):
        self.atime = when = when or datetime.utcnow()
        self.product.touch(when)

    @property
    def shape(self):
        rcl = reduce( lambda a,b: a + [b] if b else a, [self.rows, self.cols, self.levels], [])
        return tuple(rcl)

    # this doesn't belong here, database routines only plz
    # @property
    # def data(self):
    #     """
    #     numpy array with the content
    #     :return:
    #     """
    #     self.touch()
    #     if self._array is not None:
    #         return self._array
    #     self._array = zult = np.memmap(self.path, mode='r', shape=self.shape, dtype=self.dtype or 'float32')
    #     return zult

    # def close(self):
    #     if self._array is not None:
    #         self._array = None


class ContentKeyValue(Base):
    """
    key-value pairs associated with a product
    """
    __tablename__ = 'content_key_values_v0'
    product_id = Column(ForeignKey(Content.id), primary_key=True)
    key = Column(String, primary_key=True)
    value = Column(PickleType)


# singleton instance
_MDB = None


class Metadatabase(object):
    """
    singleton interface to application metadatabase
    """
    engine = None
    connection = None
    session_factory = None

    def __init__(self, uri=None, **kwargs):
        global _MDB
        if _MDB is not None:
            raise AssertionError('Metadatabase is a singleton and already exists')
        if uri:
            self.connect(uri, **kwargs)

    @staticmethod
    def instance(*args):
        global _MDB
        if _MDB is None:
            _MDB = Metadatabase(*args)
        return _MDB

    def connect(self, uri, **kwargs):
        assert(self.engine is None)
        assert(self.connection is None)
        self.engine = create_engine(uri, **kwargs)
        LOG.info('attaching database at {}'.format(uri))
        self.connection = self.engine.connect()

    def create_tables(self):
        Base.metadata.create_all(self.engine)

    def session(self):
        if self.session_factory is None:
            self.session_factory = sessionmaker(bind=self.engine)
        return self.session_factory()

    #
    # high-level functions
    #




# # ============================
# # mapping wrappers
#
# class ProductInfoAsWritableMappingAdapter(MutableMapping):
#     """
#     database Product.info dictionary adapter
#     """
#     def __init__(self, session, product, warn_on_write=True):
#         self.S = session
#         self.prod = product
#         self.wow = warn_on_write
#
#     def __contains__(self, item):
#         items = self.S.query(ProductKeyValue).filter_by(product_id=self.prod.id, key=item).all()
#         return len(items)>0
#
#     def __getitem__(self, item:str):
#         kvs = self.S.query(ProductKeyValue).filter_by(product_id=self.prod.id, key=item).all()
#         if not kvs:
#             raise KeyError("product does not have value for key {}".format(item))
#         if len(kvs)>1:
#             raise AssertionError('more than one value for %s' % item)
#
#     def __setitem__(self, key, value):
#         if self.wow:
#             LOG.warning('attempting to write to Product info dictionary in workspace??')
#         kvs = self.S.query(ProductKeyValue).filter_by(product_id=self.prod.id, key=key).all()
#         if not kvs:
#             kv = ProductKeyValue(key=key, value=value)
#             self.S.add(kv)
#             self.product.info.append(kv)
#             self.S.commit()
#         if len(kvs)>1:
#             raise AssertionError('more than one value for {}'.format(key))
#         kvs[0].value = value
#         self.S.commit()


# ============================
# support and testing routines

class tests(unittest.TestCase):
    # data_file = os.environ.get('TEST_DATA', os.path.expanduser("~/Data/test_files/thing.dat"))
    mdb = None

    def setUp(self):
        pass

    def test_insert(self):
        from datetime import datetime, timedelta
        mdb = Metadatabase.instance('sqlite://')
        mdb.create_tables()
        s = mdb.session()
        from uuid import uuid1
        uu = uuid1()
        when = datetime.utcnow()
        f = Resource(path='/path/to/foo.bar', mtime=when, atime=when, format=None)
        p = Product(uuid_str=str(uu), name='B00 Refl', obs_time=when, obs_duration=timedelta(minutes=5))
        f.product.append(p)
        p.info['test_key'] = u'test_value'
        p.info['turkey'] = u'cobbler'
        s.add(f)
        s.add(p)
        s.commit()
        p.info.update({'key': 'value'})
        self.assertEqual(p.uuid, uu)
        q = f.product[0]
        # q = s.query(Product).filter_by(resource=f).first()
        self.assertEqual(q.info['test_key'], u'test_value')
        # self.assertEquals(q[INFO.UUID], q.uuid)
        self.assertEqual(q.info['turkey'], p.info['turkey'])
        self.assertEqual(q.info['key'], p.info['key'])

def _debug(type, value, tb):
    "enable with sys.excepthook = debug"
    if not sys.stdin.isatty():
        sys.__excepthook__(type, value, tb)
    else:
        import traceback, pdb
        traceback.print_exception(type, value, tb)
        # …then start the debugger in post-mortem mode.
        pdb.post_mortem(tb)  # more “modern”



def main():
    parser = argparse.ArgumentParser(
        description="PURPOSE",
        epilog="",
        fromfile_prefix_chars='@')
    parser.add_argument('-v', '--verbose', dest='verbosity', action="count", default=0,
                        help='each occurrence increases verbosity 1 level through ERROR-WARNING-INFO-DEBUG')
    parser.add_argument('-d', '--debug', dest='debug', action='store_true',
                        help="enable interactive PDB debugger on exception")
    # http://docs.python.org/2.7/library/argparse.html#nargs
    # parser.add_argument('--stuff', nargs='5', dest='my_stuff',
    #                    help="one or more random things")
    parser.add_argument('inputs', nargs='*',
                        help="input files to process")
    args = parser.parse_args()

    levels = [logging.ERROR, logging.WARN, logging.INFO, logging.DEBUG]
    logging.basicConfig(level=levels[min(3, args.verbosity)])

    if args.debug:
        sys.excepthook = _debug

    if not args.inputs:
        unittest.main()
        return 0

    for pn in args.inputs:
        pass

    return 0


if __name__ == '__main__':
    sys.exit(main())