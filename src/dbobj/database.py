""" Database classes
"""
    
from builtins import object
from future.utils import PY3, iteritems, with_metaclass
import abc
if PY3:
    from collections.abc import Sequence, Mapping
else:
    from collections import Sequence, Mapping
from collections import OrderedDict

from .column import ColumnDesc, Column, Field, IndexColumn, IndexField
from .weakcoll import WeakColl
import copy

def c3_merge(bases):
    """ Merge together the list of base classes into the mro that will be
        created using the C3 linearisation algorithm
    """
    # Protect against empty base class lists (although this should never happens
    # because everyone derives from object *right*?)
    if not bases:
        return []
    mro = []
    # The input to c3 is the linearisation of each base class and the list of
    # bases itself
    to_merge = [b.mro() for b in bases] + [bases]
    # Non-empty lists evaluate to True, so the while loop here goes until all
    # lists are exhausted, which is the endpoint of the c3 algorithm
    while to_merge:
        # First, we have to find the first 'good' head.
        # A good head is the head of a list that does not appear in the tails of
        # any of the other lists
        try:
            head = next(l[0] for l in to_merge if not any(l[0] in l2[1:] for l2 in to_merge) )
        except StopIteration:
            raise TypeError(
                    "Failed to calculate MRO - cannot order classes {0}".format(
                        ", ".join([l[0].__name__ for l in to_merge])))
        # append it to the mro and remove it from the heads of any list
        mro.append(head)
        to_merge = [l for l in (l2[1:] if l2[0] == head else l2 for l2 in to_merge) if l]

    return mro

def resolve_attr(attr_name, dct, mro):
    """ Resolve an attribute from a MRO

        If the attribute is not present anywhere, None is returned
        Parameters:
            attr_name: The name of the attribte to resolve
            dct: The dictionary of the most derived class body
            mro: The C3 linearised list of bases
    """
    try:
        return dct[attr_name]
    except KeyError:
        pass
    for base in mro:
        try:
            return base.__dict__[attr_name]
        except KeyError:
            pass



class RowMeta(type):
    def __new__(metacls, name, bases, dct):
        # Check if any reserved attributes have been set
        reserved = ("_fields", "_index_field")
        overlap = [k for k in dct if k in reserved]
        if overlap:
            raise TypeError((
                "Class defines attributes {0} but these are reserved by the "
                "RowMeta metaclass").format(",".join(overlap) ) )
        # Figure out the MRO
        mro = c3_merge(bases)
        # Work out what the database class is
        db_cls = resolve_attr("_db_cls", dct, mro)
        if db_cls is None:
            # No associated DB => nothing else to do.
            # This probably means that this is the Row base class
            dct["_fields"] = ()
            return super(RowMeta, metacls).__new__(metacls, name, bases, dct)
        # We also have to make sure that any database classes defined in a base
        # row class are bases of this database class. If they aren't then we
        # will run into issues in the store
        for base in mro:
            try:
                base_db_cls = base.__dict__["_db_cls"]
            except KeyError:
                continue
            if not issubclass(db_cls, base_db_cls):
                raise TypeError((
                    "Associated database class {0} in row class {1} is not a "
                    "base class of {2}'s database class {3}").format(
                        base_db_cls.__name__, base.__name__, name, db_cls.__name__))

        # TODO right now this will silently overwrite any attributes with the
        # same name as a field. Should rethink this
        # Make the index field
        idx_column = getattr(db_cls, db_cls._index_column)
        idx_field_name = idx_column.name
        dct[idx_field_name] = IndexField(idx_column)
        dct["_index_field"] = idx_field_name
        # Now create the fields
        fields = []
        for column in db_cls._columns:
            dct[column.name] = Field(column)
            fields.append(dct[column.name])
        dct["_fields"] = tuple(fields)

        return super(RowMeta, metacls).__new__(metacls, name, bases, dct)

class DBMeta(abc.ABCMeta):
    def __new__(metacls, name, bases, dct):
        # Check if any reserved attributes have been set
        reserved = ("_columns", "_index_column")
        overlap = [k for k in dct if k in reserved]
        if overlap:
            raise TypeError((
                "Class defines attributes {0} but these are reserved by the "
                "DBMeta metaclass").format(",".join(overlap) ) )

        # Figure out the MRO
        mro = c3_merge(bases)
        # Get the default column class
        default_col_cls = resolve_attr("_col_cls", dct, mro)

        # Find the column descriptors and replace them with their columns. Also
        # fill them into the _columns class attribute. Note that we need to do
        # this for any columns on the base class too, as otherwise they will
        # still be using the wrong value for index. This also means that they
        # use the current class' _col_cls attribute rather than their original
        # class' (though if the ColumnDesc specifies a col_cls then this still
        # has priority)
        #
        # While the index of a column should be (largely) internal, we still
        # want it to be in some way predictable. We choose an order where the
        # lowest index corresponds to the earliest column in the least derived
        # class. The indices then count through all columns in that class, then
        # continue back through the mro up to the current class.
        #
        # The implementation of the class is in the reverse order, if one class
        # implements the same column (by name) as a less derived one, then the
        # more derived class' version is taken
        base_index_column = IndexColumn("index", ColumnDesc(is_index=True))
        column_descs = OrderedDict()
        # Least derived classes first
        for base in reversed(mro):
            this_columns = []
            if isinstance(base, DBMeta):
                for col in base._columns:
                    column_descs[col.name] = col._desc
                    this_columns.append(col.name)
                base_index_column = copy.copy(getattr(base, base._index_column) )
            # If any more derived class defines something with the same name it
            # overrides the less derived class's attribute, even if that
            # attribute is a column!
            for attr in base.__dict__:
                if attr not in this_columns:
                    try:
                        del column_descs[attr]
                    except KeyError:
                        pass
        # Finally our attributes
        # We need an extra check to make sure we don't define multiple index
        # columns inside one class
        this_idx_col = None
        for attr_name, attr in iteritems(dct):
            if isinstance(attr, ColumnDesc):
                if attr.is_index:
                    if this_idx_col is None:
                        this_idx_col = IndexColumn(attr_name, attr)
                    else:
                        raise ValueError(
                                "Multiple index columns defined on class {0}".format(name) )
                else:
                    column_descs[attr_name] = attr
        index_column = base_index_column if this_idx_col is None else this_idx_col
        dct["_index_column"] = index_column.name
        dct[index_column.name] = index_column
        columns = []
        for index, (c_name, desc) in enumerate(iteritems(column_descs) ):
            col_cls = default_col_cls if desc.col_cls is None else desc.col_cls
            if col_cls is None:
                raise ValueError(
                        "Cannot determine column class for {0}".format(c_name) )
            dct[c_name] = col_cls(c_name, index, desc)
            columns.append(dct[c_name])
        dct["_columns"] = tuple(columns)

        cls = super(DBMeta, metacls).__new__(metacls, name, bases, dct)
        # Now generate the row class
        if "_row_cls" not in cls.__dict__:
            # The bases for the row class should be the row classes of our
            # bases, where they exist
            row_bases = tuple(
                    b._row_cls for b in mro
                    if hasattr(b, "_row_cls") and b._row_cls is not None)
            cls._row_cls = RowMeta(name+"Row", row_bases, {
                "_db_cls" : cls,
                "__doc__" : "Auto generated row class for the {0} database class".format(
                    cls.__name__)})
        return cls

    def __call__(cls, *args, **kwargs):
        obj = super(DBMeta, cls).__call__(*args, **kwargs)
        # Make sure that the created object has a store
        if not hasattr(obj, "_store"):
            raise TypeError(
                    "Cannot instantiate database class {0} without _store attribute".format(
                        cls.__name__) )
        return obj

class Row(with_metaclass(RowMeta, object) ):
    def __init__(self, db, index):
        self._db = db
        # _index is stored using the store type but index is given with the
        # database type
        store_type = getattr(type(self), self._index_field).column.store_type
        self._index = index if store_type is None else store_type(index)

    @property
    def database(self):
        return self._db

    @classmethod
    def create(cls, db, **kwargs):
        """ Create a new instance of this class using the provided database
        
            Note that the database must be mutable for this to work
        """
        if db.is_associative:
            index = kwargs[cls._index_field]
            # Create the row
            db.add(**kwargs)
            # Retrieve it
            return db[index]
        else:
            db.append(**kwargs)
            return db[-1]


class DBBase(with_metaclass(DBMeta, object) ):
    """ Base class for all databases

        A database is the interface between a row and its data in the store.
        Whether a database is mutable or not depends on its store.
    """
    _col_cls = Column
    _row_cls = Row

    @property
    @abc.abstractmethod
    def is_sequential(self):
        pass

    @property
    @abc.abstractmethod
    def is_associative(self):
        pass

    @property
    def is_mutable(self):
        return self._store.is_mutable

    def __init__(self, store):
        # Check the store behaves as we need
        if self.is_sequential and not store.is_sequential:
            raise ValueError(
                "Sequential database received a non sequential store!")
        if self.is_associative and not store.is_associative:
            raise ValueError(
                "Associative database received a non associative store!")
        self._store = store
        store.add_db_ref(self)
        self._references = WeakColl()

    def __len__(self):
        return len(self._store)

    def __getitem__(self, idx):
        obj = self._row_cls(self, idx)
        self._references.append(obj)
        return obj

    def select(self, selection):
        """ Select all rows that correspond to the given selection

            selection is an iterable of True/False decisions that should be
            constructed by applying conditions to the database' columns

            Returns an iterable
        """
        return (self[idx] for (idx, sel) in zip(self, selection) if sel)

    def select_one(self, selection):
        """ Convenience method. Returns the results of select if it would
            return exactly one row and throws an exception otherwise
        """
        sel = self.select(selection)
        try:
            row = next(sel)
        except StopIteration:
            raise KeyError("No rows selected!")
        try:
            next(sel)
        except StopIteration:
            raise KeyError("More than one row selected!")
        return row

    @classmethod
    def _convert_data_for_store(cls, data):
        """ Convert the provided data into what is expected by the store """
        store_data = {}
        for col in cls._columns:
            try:
                val = data.pop(col.name)
            except KeyError:
                val = col._desc.default
                if val is ColumnDesc.NO_DEFAULT:
                    raise
            if col.store_type is not None:
                val = col.store_type(val)
            store_data[col.key] = val
        if data:
            raise KeyError("Unknown fields {0} provided".format(
                ", ".join(data) ) )
        return store_data

class SeqDatabase(DBBase, Sequence):
    """ Database with a sequential store """

    def __getitem__(self, index):
        # For sequential databases, remap negative keys to make sure that they
        # make sense
        if index < 0:
            index = len(self) + index
            if index < 0:
                raise IndexError(index)
        super(SeqDatabase, self).__getitem__(index)

    @property
    def is_sequential(self):
        return True

    @property
    def is_associative(self):
        return False

    def _remap_indices(self, remap):
        """ Reassign row indices 

            This is called if for some reason the underlying row indices change
            (for instance, if a row is deleted. remap should be a mapping of old
            index to new index.
        """
        for row in self._references:
            try:
                row._index = remap[row._index]
            except KeyError:
                pass

    def __delitem__(self, row):
        """ Remove a row """
        del self._store[row._index]
        self._references.remove(row)

    def append(self, **row_data):
        """ Add a new row with the supplied data """
        self._store.append(self._convert_data_for_store(row_data) )
        return self[-1]

class AssocDatabase(DBBase, Mapping):
    """ Database with an associative store """

    @property
    def is_sequential(self):
        return False

    @property
    def is_associative(self):
        return True

    def __iter__(self):
        return iter(self._store)

    def add(self, **row_data):
        """ Add a new row with the supplied index and data """
        # First get the index
        index = row_data.pop(self._index_column)
        store_type = getattr(type(self), self._index_column).store_type
        store_index = index if store_type is None else store_type(index)
        self._store.add(store_index, self._convert_data_for_store(row_data) )
        return self[index]
