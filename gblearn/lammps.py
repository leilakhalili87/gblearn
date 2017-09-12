"""Functions for interacting with LAMMPS dump files.
"""
import numpy as np
from gblearn import msg
class Dump(object):
    """Represents a dump file that could potentially have more than one
    timestep.

    Args:
        filepath (str): full path to the LAMMPS dump file to extract the
          time step from.
        stepfilter (list): of `int` timestep values that *should* be parsed. If the
          next timestep encountered is not in this list, it will be ignored.

    Attributes:
        steps (dict): keys are `int` timestep ids; values are :class:`Timestep`.
    """
    def __init__(self, filepath, stepfilter=None):
        from os import path
        self.filepath = path.abspath(path.expanduser(filepath))
        self.steps = {}

        with open(self.filepath) as f:
            t = Timestep(self.filepath, None, f, stepfilter)
            while len(t) > 0:
                self.steps[t.index] = t
                t = Timestep(self.filepath, None, f, stepfilter)

    def __len__(self):
        return len(self.steps)
    def __iter__(self):
        return iter(self.steps.items())
    def __contains__(self, ts):
        return ts in self.steps
    def __getitem__(self, ts):
        return self.steps[ts]

    def __eq__(self, other):
        if not isinstance(other, Dump):
            return False
        return all(a == b for a, b in zip(self, other))
    
    def dump(self, filename, mode='w', rebox=False):
        """Dumps the specified structure to file in the LAMMPS format.

        Args:
            filename (str): target path for saving the timestep.
            mode (str): file read/write mode; defaults to new file.
            rebox (bool): when True, the box is recalculated from the current
              set of atomic positions.
        """
        from os import path
        filepath = path.abspath(path.expanduser(filename))
        if mode == 'w':
            open(filepath, 'w').close()            
        for t, ts in self:
            ts.dump(filename, rebox=rebox)
    
class Timestep(object):
    """Represents a single time step in a LAMMPS dump file.

    Args:
        filepath (str): full path to the LAMMPS dump file to extract the
          time step from.
        index (int): index of the time step in the dump file.
        openf (file): open file object for sequential instantiation of multiple time
          steps.
        stepfilter (list): of `int` timestep values that *should* be parsed. If the
          next timestep encountered is not in this list, it will be ignored.

    Attributes:
        types (numpy.ndarray): integer types of the atoms in the list.
        ids (numpy.ndarray): integer atom ids; has same length as `len(self)`.
        xyz (numpy.ndarray): with shape (len(self), 3); float positions of the
          atoms in the time step.
        periodic (list): of `bool` specifying whether the `x`, `y`, or `z`
          directions are periodic (for the box).
        box (numpy.ndarray): with shape (3, 2) specifying the `lo` and `hi` bounds
          of the box in each direction.
        extras (list): of `str` indicating extra atomic parameters that are
          available in this time step.
    """
    def __init__(self, filepath, index=0, openf=None, stepfilter=None):
        self.filepath = filepath
        self.index = index
        
        raw = self._read(openf, stepfilter)
        #We should at least have time, type, id, xyz, box, periodic; otherwise
        #this is an incomplete dump file.
        if len(raw) < 6:
            self.types = []
            self.ids = []
            self.xyz = []
            self.box = None
            self.periodic = None
            self.extras = None
            return

        self.types = np.array(raw["type"], int)
        self.ids = np.array(raw["id"], int)
        self.xyz = np.array(raw["xyz"])
        self.extras = ["ids"]
        for key in raw:
            if "atom:" not in key:
                continue
            quant = key.split(':')[1]
            self.extras.append(quant)
            setattr(self, quant, np.array(raw[key]))

        self.periodic = tuple(map(lambda p: p == "pp", raw["periodic"]))
        self.box = np.array(raw["box"])
        if len(self.xyz) != raw["natoms"]:# pragma: no cover
            wmsg = "File {} did not have as many atoms ({}/{}) as specified."
            msg.warn(wmsg.format(self.filepath), len(self.xyz), raw["natoms"])

    def __len__(self):
        return len(self.xyz)
    def __eq__(self, other):
        if not isinstance(other, Timestep):
            return False
        return (np.allclose(self.xyz, other.xyz) and
                np.allclose(self.ids, other.ids) and
                np.allclose(self.types, other.types) and
                self.extras == other.extras and
                np.allclose(self.box, other.box) and
                self.periodic == other.periodic)

    def gb(self, Z=None, method="median", pattr="c_csd", extras=True, soapargs={},
           **kwargs):
        """Returns the grain boundary for this time step.

        Args:
            Z (int or list): element code(s) for the atomic species.
            method (str): one of ['median'].
            pattr (str): name of an attribute in :attr:`extras` to pass as the
              selection parameter of the routine.
            extras (bool): when True, include extra attributes in the new GB
              structure.
            soapargs (dict): initialization parameters for the
              :class:`gblearn.soap.SOAPCalculator` instance for the GB.
            kwargs (dict): additional arguments passed to the atom selection
              function. For `median`, see :func:`gblearn.selection.median` for the
              arguments.
        
        Returns:
            gblearn.gb.GrainBoundary: instance with only those atoms that appear
              to be at the boundary.
        """
        if Z is None:
            raise ValueError("`Z` is a required parameter for constructing a "
                             ":class:`GrainBoundary` instance.")
        
        from gblearn.gb import GrainBoundary
        ids = self.gbids(method, pattr, **kwargs)
        if extras:
            x = {k: getattr(self, k)[ids] for k in self.extras}
        else:
            x = None
        result = GrainBoundary(self.xyz[ids,:], self.types[ids],
                               self.box, Z, extras=x, **soapargs)
        return result
        
    def gbids(self, method="median", pattr="c_csd", **kwargs):
        """Returns the *indices* of the atoms that lie at the grain
        boundary.

        Args:
            method (str): one of ['median', 'cna'].
            pattr (str): name of an attribute in :attr:`extras` to pass as the
              selection parameter of the routine.
            kwargs (dict): additional arguments passed to the atom selection
              function. For `median`, see :func:`gblearn.selection.median` for the
              arguments.

        Returns:
            numpy.ndarray: of integer indices of atoms in this timestep that are
              considered to lie on the boundary.

        Examples:
            Retrieve the positions of the atoms that lie at the boundary using the
            median centro-symmetry parameter values.

            >>> from gblearn.lammps import Timestep
            >>> t0 = Timestep("lammps.dump")
            >>> ids = t0.gbids()
            >>> xyz = t0.xyz[ids,:]
        """
        import gblearn.selection as sel
        methmap = {
            "median": sel.median,
            "cna": sel.cna_max
            }
        if method in methmap:
            return methmap[method](self.xyz, getattr(self, pattr),
                                   types=self.types, **kwargs)
    
    def dump(self, filename, mode='a', rebox=False):
        """Dumps the specified structure to file in the LAMMPS format.

        Args:
            filename (str): target path for saving the timestep.
            mode (str): file read/write mode; defaults to append.
            rebox (bool): when True, the box is recalculated from the current
              set of atomic positions.
        """
        with open(filename, mode) as f:
            f.write("ITEM: TIMESTEP\n")
            f.write("{}\n".format(self.index))
            f.write("ITEM: NUMBER OF ATOMS\n")
            f.write("{0:d}\n".format(len(self)))

            speriod = ' '.join([("pp" if p else "ss") for p in self.periodic])
            f.write("ITEM: BOX BOUNDS {}\n".format(speriod))
            if rebox:
                from gblearn.selection import extent
                minmax = [extent(self.xyz, i) for i in range(3)]
            else:
                minmax = self.box
                
            for i in range(len(minmax)):
                f.write("{0:.4f} {1:.4f}\n".format(*minmax[i]))

            xheads = ' '.join(self.extras)
            f.write("ITEM: ATOMS id type x y z {}\n".format(xheads))

            #We need to generate a format string for all the extra quantities.
            xfmtstr = ' '
            xfmt = []
            for ix, x in enumerate(self.extras):
                dtype = type(getattr(self, x)[0])
                if dtype is np.int64:
                    xfmt.append("{{{0:d}:d}}".format(ix+5))
                elif dtype is np.float64:
                    xfmt.append("{{{0:d}:.5e}}".format(ix+5))
            xfmtstr += ' '.join(xfmt)

            atomfmt = "{0:d} {1:d} {2:.5f} {3:.5f} {4:.5f}"
            atomfmt += "{}\n".format(xfmtstr)
            for iatom, xyz in enumerate(self.xyz):
                #1 4 -65.9625 1.54915 1.46824 5 30.976
                sid, atype = self.ids[iatom], self.types[iatom]
                x, y, z = xyz
                xvals = [getattr(self, xname)[iatom] for xname in self.extras]
                f.write(atomfmt.format(sid, atype, x, y, z, *xvals))
    
    def _read(self, openf=None, stepfilter=None):
        """Reads the defining items from the dump file for the timestep
        configured in this object.

        Args:
            openf (file): if this timestep is being read in from a higher-level
              routine, an open file object to read from. This allows sequential time
              steps to be read without re-seeking over parts of the file.

        Returns:
            dict: with keys ['type', 'id', 'xyz', 'atom:{}'...], where the 'atom:{}'
              keys are for extra labelled quantities in the dump file that are
              applicable to each atom.
        """
        itemstack = []
        current = None
        result = {}
        xkeys = None
        timeskip = False
        laststep = False

        if openf is None:
            f = open(self.filepath)
        else:
            f = openf

        line = 'start'
        while line != '':
            lastpos = f.tell()
            line = f.readline()
            if line == '':
                continue
            
            if itemstack is not None and len(itemstack) > 0:                    
                cast = itemstack.pop()
                raw = line.split()
                values = [t(r) for t, r in zip(cast, raw)]
                if len(values) == 1:
                    values = values[0]

                if current == "time":
                    if stepfilter is not None and values not in stepfilter:
                        timeskip = True
                    elif (self.index is not None and values != self.index):
                        if values > self.index:
                            if openf is None:
                                return {}
                            else:
                                timeskip = True
                                laststep = True
                        else:
                            timeskip = True
                    elif self.index is None:
                        self.index = values
                    else:
                        timeskip = False
                
                if len(itemstack) == 0 and current not in result:
                    result[current] = values
                else:
                    if current not in result:
                        result[current] = []
                    result[current].append(values)
                continue
            elif itemstack is None and current == "atoms":
                if "ITEM" in line:
                    current = None
                    if openf is not None:
                        f.seek(lastpos)
                    break
                else:
                    #E.g. line: 1 4 -65.9625 1.54915 1.46824 5 30.976 
                    vals = line.split()
                    sid, atype = tuple(map(int, vals[0:2]))
                    result["type"].append(atype)
                    result["id"].append(sid)
                    x, y, z = tuple(map(float, vals[2:5]))
                    result["xyz"].append((x, y, z))
                    if len(vals) > 5 and xkeys is not None:
                        for ikey, v in enumerate(vals[5:]):
                            result[xkeys[ikey]].append(eval(v))
                    continue # pragma: no cover
                
            if "ITEM: TIMESTEP" in line:
                if laststep:
                    f.seek(lastpos)
                    break
                itemstack.append((int,))
                current = "time"
                timeskip = False
            elif not timeskip:
                if "ITEM: NUMBER OF ATOMS" in line:
                    itemstack.append((int,))
                    current = "natoms"
                elif "ITEM: BOX BOUNDS" in line:
                    period = line.strip().split("BOX BOUNDS")
                    if len(period) == 2 and period[1] != '':
                        result["periodic"] = period[1].strip().split()
                    else:
                        result["periodic"] = ("ss", "ss" ,"ss")
                    itemstack.extend([(float, float)]*3)
                    current = "box"
                elif "ITEM: ATOMS" in line:
                    itemstack = None
                    current = "atoms"
                    result["type"] = []
                    result["id"] = []
                    result["xyz"] = []
                    
                    #The first two headings in the line have "ITEM: ATOMS", the
                    #rest are usuall id, type, x, y, z, rest...
                    headings = line.split()
                    extras = len(headings) > 7
                    if extras:
                        xkeys = []
                        xheadings = headings[7:]
                        for xhead in xheadings:
                            key = "atom:{}".format(xhead)
                            result[key] = []
                            xkeys.append(key)
            
        if openf is None:
            #Close the file since we opened it.
            f.close()
            
        return result