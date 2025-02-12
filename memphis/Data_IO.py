# -*- coding: utf-8 -*-
"""
Created on Wed Feb 21 10:40:53 2018

@author: jpelda
"""

import os
import sys

import configparser as cfp
import pandas as pd
import geopandas as gpd
from shapely.geometry import Point, Polygon
from shapely.wkt import loads
from sqlalchemy import create_engine
from osgeo import ogr
import osmnx

sys.path.append(os.path.dirname(os.getcwd()) + os.sep + 'utils')
from transformations_of_crs_values import transform_coords


class Data_IO:
    """Access to all sql queries. Initialised by config.ini.

    Parameters
    ----------
    fname_config : str
        the config filename
    """
    def __init__(self, fname_config):
        print('Load config from {}'.format(fname_config))
        self.config = cfp.ConfigParser()
        self.config.read(fname_config)
        self.engine = create_engine(self.config['SQL']['db'], echo=False,
                                    encoding='utf-8')

        GIS = self.config['SQL_QUERIES']
        self.x_min = float(GIS['x_min'])
        self.y_min = float(GIS['y_min'])
        self.x_max = float(GIS['x_max'])
        self.y_max = float(GIS['y_max'])
        coords = ((self.x_min, self.y_min),
                  (self.x_min, self.y_max),
                  (self.x_max, self.y_max),
                  (self.x_max, self.y_min),
                  (self.x_min, self.y_min))
        self.bbox = Polygon(coords)
        self.coord_system = GIS['coord_system']
        self.country = GIS['country']
        self.sewage_network = GIS['sewage_network']
        self.census = GIS['census']
        self.districts = GIS['districts']

        self.inhabs = 'None'
        RASTER = self.config['raster']
        self.coord_system_raster = RASTER['coord_system']
        if self.coord_system_raster != 'None' and \
                RASTER['inhabitants'] != 'None':
            self.inhabs = int(RASTER['inhabitants'])
        self.partial_map = loads(RASTER['partial_map'])

        wwtp = [c[1].split(', ') for c in list(self.config['wwtp'].items())]
        self.wwtp = [Point(float(c[0]), float(c[1])) for c in wwtp]

        self.city = self.config['Files']['city']
        self.path_export = eval(self.config['Files']['path_export']) + os.sep +\
                           self.city + os.sep
        self.path_export_fig = self.path_export + 'fig' + os.sep
        self.path_export_shp = self.path_export + 'shp' + os.sep
        self.path_import = eval(self.config['Files']['path_import'])

    def write_to_sqlServer(self, table_name, df, dtype={}):
        """Writes to SQL-Database.

        Parameters
        ----------
            table_name : str
                name of table to write to
            df : pandas.DataFrame()
                which values are written to table
            dtype : dict, optional
                {col: type} type is of int, float, or geometry etc.
        """
        print('Save data to {} in db {}'.format(table_name, self.engine))

        if 'GEOMETRY' in dtype.values():
            name = list(dtype.keys())[list(dtype.values()).index('GEOMETRY')]

            col = [x + ' ' + dtype[x] for x in dtype]
            print(', '.join(col))
            sql_new_table = ("CREATE OR REPLACE TABLE `{}` "
                             "({})COLLATE='utf8_bin'").format(table_name,
                                                              ', '.join(col))
            print(sql_new_table)
            df[name] = ["ST_GEOMFROMTEXT('{}', {})".format(x, 4326) for
                        x in df[name]]

            data = list(df.itertuples(index=False, name=None))
            data = str(data).strip('[]')
            data = data.replace('"', '')

            sql = "INSERT INTO `{}` ({}) VALUES {}".format(
                    table_name, ', '.join(dtype.keys()), data)

            conn = self.engine.connect()
            conn.execute(sql_new_table)
            conn.execute(sql)
            conn.close()

        else:
            df.to_sql(table_name, self.engine, if_exists='replace',
                      index=False)

        print("Saved.")

    def read_from_sqlServer(self, name, all=False):
        """Reads SQL-Database into pandas dataframe.

        Parameters
        ----------
        sql_query : str
            name of sql-query in section SQL_QUERIES in config-file
        Returns
        -------
            pandas.DataFrame(sql-db)
        """

        conf = eval(self.config['SQL_QUERIES'][name])
        table = conf['table']
        col = conf['col']

        if 'coord_system' in conf.keys():
            coord_system = conf['coord_system']

            if coord_system == self.coord_system:
                bbox = self.bbox
            elif coord_system != self.coord_system and\
                    coord_system is not None:
                bbox = transform_coords([self.bbox],
                                        from_coord=self.coord_system,
                                        into_coord=coord_system)[0]

        # TODO change import via ST_GEOMFROMTEXT to WKB import.
        # https://www.gaia-gis.it/gaia-sins/spatialite-cookbook/html/wkt-wkb.html
        # Import as string = ST_AsBinary(name)
        # ST_GeomFromWKB(x'01010000008D976E1283C0F33F16FBCBEEC9C30240')
        # shapely.wkb.loads(string)
        if 'SHAPE' in col.keys() and all is not True:
            if len(col['SHAPE']) is 1:
                sql = self.select_from_where_mbrContains(col, table, bbox)
            elif len(col['SHAPE']) is 2:
                sql = self.select_from_where_between(col, table, bbox)

        elif all is True:
            sql = self.select_from(col, table)
        else:
            sql = self.select_from(col, table)

        df = pd.read_sql(sql, self.engine)
        if 'coord_system' in conf.keys():
            if coord_system == self.coord_system:
                if len(col['SHAPE']) is 1:
                    #  TODO find lgenth of x and y values
                    df['SHAPE'] = df[col['SHAPE'][0]].map(loads)

            elif coord_system != self.coord_system and\
                    coord_system is not None:
                if len(col['SHAPE']) == 1:
                    #  TODO find length of x and y values
                    df['SHAPE'] = transform_coords(
                        [df[col['SHAPE'][0]].map(loads)],
                        from_coord=coord_system,
                        into_coord=self.coord_system)
                elif len(col['SHAPE']) == 2:
                    df['len_x'] = len(set(df[col['SHAPE'][0]]))
                    df['len_y'] = len(set(df[col['SHAPE'][1]]))
                    SHAPEmetry = transform_coords(
                        [Point(x, y) for x, y in zip(
                                df[col['SHAPE'][0]],
                                df[col['SHAPE'][1]])],
                        from_coord=coord_system,
                        into_coord=self.coord_system)
                    df['SHAPE'] = SHAPEmetry
        if 'ST_ASText(SHAPE)' in df.keys():
            del df['ST_ASText(SHAPE)']
        #  Rename columns to fit names in algorithm.
        inv_col = {v: k for k, v in col.items() if type(v) != list}
        df = df.rename(columns=(inv_col))
        return df

    def select_from_where_between(self, col, table, bbox):
        sql = ("SELECT {} FROM {} WHERE {} BETWEEN {} and {} and "
               "{} BETWEEN {} and {}").format(
                       ', '.join(self.dict_of_nested_lists_to_list(col)),
                       table, col['SHAPE'][0],
                       min(bbox.exterior.coords.xy[0]),
                       max(bbox.exterior.coords.xy[0]),
                       col['SHAPE'][1],
                       min(bbox.exterior.coords.xy[1]),
                       max(bbox.exterior.coords.xy[1]))
        return sql

    def select_from_where_mbrContains(self, col, table, bbox):
        sql = ("SELECT {} FROM {} WHERE MBRContains({}, ST_GEOMFROMTEXT({})) = 1"
               ).format(', '.join(self.dict_of_nested_lists_to_list(col)),
                        table, self.st_geofromtext_geometry(bbox),
                        ', '.join(col['SHAPE']))
        return sql

    def st_geofromtext_geometry(self, geometry):
        geom = ogr.CreateGeometryFromWkb(geometry.wkb)
        return ("ST_GEOMFROMTEXT('{}')").format(geom)

    def select_from(self, col, table):
        sql = ("SELECT {} FROM {}").format(
            ', '.join(self.dict_of_nested_lists_to_list(col)), table)
        return sql

    def write_gdf_to_file(self, gdf, fname=''):
        """Writes to File.

        Parameters
        ----------
        fname : str
            fname can be set in Data_IO.__init__.city.
        gdf : geopandas.GeoDataFrame()
            Which values are written to file. File format is given by suffix.
        """
        if fname.endswith('shp'):
            fname = self.path_export_shp + fname
        else:
            fname = self.path_export

        gdf.to_file(filename=fname)
        print("Saved {} \n to {}".format(gdf.keys(), fname))

    def read_from_shp(self, name, path=None):
        """Reads File into pandas dataframe.
        Parameters
        ----------
        name: str
            name of file in section Files in config

        Returns
        -------
            geopandas.DataFrame(fname)
        """
        if path is None:
            path = self.path_import
        fname = self.config['Files'][name]
        df = gpd.read_file(path + fname)
        return df

    def read_from_graphml(self, name, path=None):
        """Reads File into pandas dataframe.

        Parameters
        ----------
        name : str
            name of file in section Files in config

        Returns
        -------
            nx.Graph()
        """
        if path is None:
            path = self.path_import

        graph = osmnx.save_load.load_graphml(path + os.sep +
                                             self.config['Files'][name])
        return graph

    def dict_of_nested_lists_to_list(self, dictionary):
        dictionary = [dictionary[x] for x in dictionary.keys()]
        arr = []
        for item in dictionary:
            if type(item) == list:
                for x in item:
                    arr.append(x)
            else:
                arr.append(item)
        ret = arr
        return ret

    def save_figure(self, fig, name='', path_export=None):
        """

        Parameters
        ----------
        fig : matplotlib.figure()
        name : str
        If an extension to Data.city is wanted.
        path_export : str
        Is given via Data.path_export_fig / Data.path_export_shp ...

        Returns
        -------

        """
        if path_export:
            fname = "{}{}{}_{}".format(path_export, name)
        else:
            if name == '':
                fname = self.path_export_fig + self.city.upper()
            else:
                fname = self.path_export_fig + self.city.upper() +\
                    '_' + name

        fig.savefig(fname + '.pdf', filetype='pdf', bbox_inches='tight',
                    dpi=1200, pad_inches=0.01)
        fig.savefig(fname + '.png', filetype='png', bbox_inches='tight',
                    dpi=1200, pad_inches=0.01)


if __name__ == "__main__":

    Data = Data_IO(os.path.dirname(os.getcwd()) + os.sep + 'config' + os.sep +
                   'test_config.ini')

    gis = Data.read_from_sqlServer('gis')
    census = Data.read_from_sqlServer('census')


else:
    pass
