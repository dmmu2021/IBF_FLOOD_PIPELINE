import netCDF4
import xarray as xr
import numpy as np
import os
from os import listdir
from os.path import isfile, join
import pandas as pd
from pandas import DataFrame 
import rioxarray
import rasterio as rio
from tqdm import tqdm 
import geocube 
import re
from geocube.api.core import make_geocube
import geopandas as gpd
import sys
import json
import datetime
import urllib.request
import urllib.error
import tarfile
import time
from ftplib import FTP
#import cdsapi
from flood_model.dynamicDataDb import DatabaseManager
from flood_model.settings import *
try:
    from flood_model.secrets import *
except ImportError:
    print('No secrets file found.')
import os
import logging
logger = logging.getLogger(__name__)


class GlofasData:

    def __init__(self, leadTimeLabel, leadTimeValue, countryCodeISO3, glofas_stations, district_mapping,admin_df):
        #self.db = DatabaseManager(leadTimeLabel, countryCodeISO3)
     
        self.leadTimeLabel = leadTimeLabel
        self.admin_area_gdf=admin_df
        self.leadTimeValue = leadTimeValue
        self.countryCodeISO3 = countryCodeISO3
        self.GLOFAS_FILENAME=SETTINGS[countryCodeISO3]['GLOFAS_FILENAME']
        self.GLOFAS_GRID_FILENAME=GLOFAS_GRID_FILENAME 
             
        self.glofasReturnPeriod=SETTINGS[countryCodeISO3]['glofasReturnPeriod']
        self.GLOFAS_FTP=SETTINGS[countryCodeISO3]['GLOFAS_FTP']
        self.TRIGGER_LEVELS=SETTINGS[countryCodeISO3]['TRIGGER_LEVELS']
        self.eapAlertClass=SETTINGS[countryCodeISO3]['eapAlertClass']
        self.selectedPcode = SETTINGS[countryCodeISO3]['selectedPcode']
        self.inputPath = PIPELINE_DATA+'input/glofas/'
        self.inputPathGrid = PIPELINE_DATA+'input/glofasgrid/'
        
        self.glofasAdmnPerDay=PIPELINE_OUTPUT + 'glofas_extraction/glofas_Admin_extraction' + countryCodeISO3 + '.csv'
        
        
        self.triggerPerDay = PIPELINE_OUTPUT + \
            'triggers_rp_per_station/trigger_per_day_' + countryCodeISO3 + '.json'
        self.extractedGlofasDir = PIPELINE_OUTPUT + 'glofas_extraction'
        if not os.path.exists(self.extractedGlofasDir):
            os.makedirs(self.extractedGlofasDir)
        self.extractedGlofasPath = PIPELINE_OUTPUT + \
            'glofas_extraction/glofas_forecast_' + \
            self.leadTimeLabel + '_' + countryCodeISO3 + '.json'
        self.triggersPerStationDir = PIPELINE_OUTPUT + 'triggers_rp_per_station'
        if not os.path.exists(self.triggersPerStationDir):
            os.makedirs(self.triggersPerStationDir)
        self.triggersPerStationPath = PIPELINE_OUTPUT + \
            'triggers_rp_per_station/triggers_rp_' + \
            self.leadTimeLabel + '_' + countryCodeISO3 + '.json'
        self.GLOFAS_STATIONS = glofas_stations
        self.DISTRICT_MAPPING = district_mapping
        self.current_date = CURRENT_DATE.strftime('%Y%m%d')
        self.placecodeLen= SETTINGS[countryCodeISO3]['placecodeLen'] 
        self.placeCodeInitial= SETTINGS[countryCodeISO3]['placeCodeInitial'] 

    def process(self):
        if SETTINGS[self.countryCodeISO3]['mock'] == True:
            self.extractMockData()
            self.findTrigger()
        else:
            self.removeOldGlofasData()
            self.download()
            if self.countryCodeISO3=='SSD':
                #self.extractGlofasDataGridToCsv()
                self.extractGlofasDataGrid()
                self.findTrigger()
            else:
                self.getGlofasData()
                self.extractGlofasData()
                self.findTrigger()


    def removeOldGlofasData(self):
        for filepath in [self.inputPath,self.inputPathGrid]:
            if os.path.exists(filepath):
                for f in [f for f in os.listdir(filepath)]:
                    os.remove(os.path.join(filepath, f))
            else:
                os.makedirs(filepath)

    def download(self):
        downloadDone = False

        timeToTryDownload = 43200
        timeToRetry = 600

        start = time.time()
        end = start + timeToTryDownload

        while downloadDone == False and time.time() < end:
            try:
                #self.getGlofasData()
                self.start_download_loop()
                downloadDone = True
            except Exception as exception:
                error = 'Download data failed. Trying again in {} minutes.\n{}'.format(timeToRetry//60, exception)
                logger.error(error)
                time.sleep(timeToRetry)
        if downloadDone == False:
            raise ValueError('GLofas download failed for ' +
                            str(timeToTryDownload/3600) + ' hours, no new dataset was found')
    def makeFtpRequest(self):
            filename = self.GLOFAS_FILENAME + '_' + self.current_date + '00.tar.gz'
            ftp_path = 'ftp://'+GLOFAS_USER +':'+GLOFAS_PW + '@' + self.GLOFAS_FTP


            urllib.request.urlretrieve(ftp_path + filename,self.inputPath + filename)
            #urllib.request.urlretrieve(ftp_path + filename,'/mnt/containermnt/glofas.nc')
            
            
    def makeFtpRequestNcFiles(self):
            filename = self.GLOFAS_GRID_FILENAME + '_' + self.current_date + '00.nc'
            ftp_path = 'ftp://'+GLOFAS_USER +':'+GLOFAS_PW + '@' + self.GLOFAS_FTP         
            urllib.request.urlretrieve(ftp_path + filename,self.inputPathGrid + filename)
            #urllib.request.urlretrieve(ftp_path + filename,'/mnt/containermnt/glofas.nc')

    def downloadFtpChunks(self):
        """
        Download a glofas data from a URL in chunks and save it to a local file.
        extract data for admin area
        generate csv files for each admin area
        """
        # Open a connection to the URL
        bf_gpd=self.admin_area_gdf
  
        bf_gpd['pcode']=bf_gpd['placeCode'].apply(lambda x:int(x[len(self.countryCodeISO3):]))
        bbox_bfs=list(bf_gpd.total_bounds)

        for ens in range (0,51):
            ensamble="{:02d}".format(ens)
            logger.info(f'start downloading data for ensamble {ens}')


            url = f'ftp://{GLOFAS_USER}:{GLOFAS_PW}@{GLOFAS_FTP}/fc_netcdf/{self.current_date}/dis_{ensamble}_{self.current_date}00.nc'             
            chunk_size=8192

            Filename = self.inputPathGrid + 'glofas.nc'

            with urllib.request.urlopen(url) as response:
                # Open the local file for writing the downloaded content       
                with open(Filename, 'wb') as out_file:
                    # Initialize a counter for the downloaded bytes
                    bytes_downloaded = 0

                    while True:
                        # Read a chunk from the response
                        chunk = response.read(chunk_size)
                        # If the chunk is empty, we have downloaded the entire file
                        if not chunk:
                            break
                        # Write the chunk to the local file
                        out_file.write(chunk)
                        # Update the counter
                        bytes_downloaded += len(chunk)
                        #print(f"Downloaded {bytes_downloaded} bytes")
            if out_file:
                out_file.close()
            logger.info(f'finished downloading data for ensamble {ens}')
            nc_file = xr.open_dataset(Filename)  
            var_data =nc_file.sel(lat=slice(bbox_bfs[3], bbox_bfs[1]),lon=slice(bbox_bfs[0], bbox_bfs[2]))           
            df_leadtime_ens=[]

            for i in range(0,7):
                leadTimelabel=str(i+1)+'_day'  
                nc_p =var_data.sel(time=var_data.time.values[i]).drop(['time']).rio.write_crs("epsg:4326", inplace=True)

                out_grid = make_geocube(
                    vector_data=bf_gpd,
                    measurements=['pcode'],
                    like=nc_p)
                
                out_grid=out_grid.rename({'x': 'lon','y': 'lat'})
                for gof_var in ['dis']:
                    glofas_rtp=nc_p[gof_var]       
                    out_grid[gof_var] = (glofas_rtp.dims, glofas_rtp.values)     

                zonal_stats_df = (out_grid.groupby(out_grid['pcode']).max().to_dataframe().reset_index())
                zonal_stats_df['pcode']=zonal_stats_df['pcode'].apply(lambda x:self.placeCodeInitial + str(int(x)).zfill(self.placecodeLen))
                zonal_stats_df['ensemble']=ens
                zonal_stats_df[f'dis_{ensamble}']= zonal_stats_df['dis']
                
                zonal_stats_df['leadTime']=leadTimelabel
                df_leadtime_ens.append(zonal_stats_df.filter(['pcode','ensemble','leadTime','dis','rl2','rl5','rl20']))

            glofasDffinal = pd.concat(df_leadtime_ens) 

            FilenameCsv = self.inputPathGrid + f'glofas_{ens}.csv'
            logger.info(f'saved csv files for ensamble {ens}')

            glofasDffinal.to_csv(FilenameCsv) 
            #glofasDffinal.to_csv(f'/mnt/containermnt/glofas_{ens}.csv')  
            nc_file.close()
        logger.info(f'finished downloading data per chunk')


    def start_download_loop(self):
      downloadDone = False
      timeToTryDownload = 43200
      timeToRetry = 600
      start = time.time()
      end = start + timeToTryDownload
      while downloadDone == False and time.time() < end:
          try:
              if self.countryCodeISO3=='SSD':
                  #self.makeFtpRequestNcFiles()
                  logger.info('Dounloading data for south sudan')
                  self.downloadFtpChunks()
              else:
                   self.makeFtpRequest()
    #           makeApiRequest()
              downloadDone = True
          except:
              error = 'Download data failed. Will be trying again in ' + str(timeToRetry/60) + ' minutes.'
              logger.error(error)
              time.sleep(timeToRetry)
      if downloadDone == False:
          logger.error('GLofas download failed for ' +
                          str(timeToTryDownload/3600) + ' hours, no new dataset was found')
          raise ValueError('GLofas download failed for ' +
                          str(timeToTryDownload/3600) + ' hours, no new dataset was found')
                          
    def getGlofasData(self):
        filename = self.GLOFAS_FILENAME + '_' + self.current_date + '00.tar.gz'
        path = 'glofas/' + filename
        
        #glofasDataFile = self.db.getDataFromDatalake(path)
        #if glofasDataFile.status_code >= 400:
        #    raise ValueError()
        #open(self.inputPath + filename, 'wb').write(glofasDataFile.content)
        tar = tarfile.open(self.inputPath + filename, "r:gz")
        tar.extractall(self.inputPath)
        tar.close()
        
    def checkTriggerProb(self,num):
        if self.countryCodeISO3 in ['ZMB']:
            if num <= self.eapAlertClass['no']:
                return "no"
            elif  num < self.eapAlertClass['min']:
                return "min"
            elif  num < self.eapAlertClass['med']:
                return "med"
            elif  num >= self.eapAlertClass['max']:
                return "max"
        else:
            if num >= self.eapAlertClass['max']:
                return "max"
            else:
                return "no"
            
    def extractGlofasDataGridToCsv(self):
            
        bf_gpd=self.admin_area_gdf 
        
        bf_gpd['pcode']=bf_gpd['placeCode'].apply(lambda x:int(x[len(self.countryCodeISO3):]))
        
        bbox_bfs=list(bf_gpd.total_bounds)
        
        # Load input data
        filename = self.GLOFAS_GRID_FILENAME + '_' + self.current_date + '00.nc'
        #filename ='/mnt/containermnt/glofas.nc'
        Filename = os.path.join(self.inputPathGrid, filename) 
        nc_file = xr.open_dataset(Filename)  
        var_data =nc_file.sel(lat=slice(bbox_bfs[3], bbox_bfs[1]),lon=slice(bbox_bfs[0], bbox_bfs[2]))           
        df_leadtime_ens=[]

        for i in range(0,7):
            leadTimelabel=str(i+1)+'_day'  
            for ens in range(0,51):
                nc_p =var_data.sel(time=var_data.time.values[i],ensemble=var_data.ensemble.values[ens]).drop(['time','ensemble']).rio.write_crs("epsg:4326", inplace=True)

                out_grid = make_geocube(
                    vector_data=bf_gpd,
                    measurements=['pcode'],
                    like=nc_p)
                
                out_grid=out_grid.rename({'x': 'lon','y': 'lat'})
                for gof_var in ['dis','rl2','rl5','rl20']:
                    glofas_rtp=nc_p[gof_var]       
                    out_grid[gof_var] = (glofas_rtp.dims, glofas_rtp.values)     

                zonal_stats_df = (out_grid.groupby(out_grid['pcode']).max().to_dataframe().reset_index())
                zonal_stats_df['pcode']=zonal_stats_df['pcode'].apply(lambda x:self.placeCodeInitial + str(int(x)).zfill(self.placecodeLen))
                zonal_stats_df['ensemble']=ens+1
                
                zonal_stats_df['leadTime']=leadTimelabel
                df_leadtime_ens.append(zonal_stats_df.filter(['pcode','ensemble','leadTime','dis','rl2','rl5','rl20']))
 
        glofasDffinal = pd.concat(df_leadtime_ens) 
        
        glofasDffinal.to_csv(self.glofasAdmnPerDay) 

        logger.info('Extracted Glofas data from grid- discharge per day File saved') 
            
        nc_file.close()
        
        
          
    def extractGlofasDataGrid(self):
        trigger_per_day = {
            '1-day': False,
            '2-day': False,
            '3-day': False,
            '4-day': False,
            '5-day': False,
            '6-day': False,
            '7-day': False,
        }       
        #glofasDffinal.to_csv(f'glofas_{ensamble}.csv') 

        csv_files = [f'{self.inputPathGrid}glofas_{f}.csv' for f in range (0,51) ]

        # Create an empty list to hold dataframes
        dfs = []

        # Read each CSV file into a dataframe and append to the list
        for csv_file in csv_files:
            df = pd.read_csv(csv_file)
            dfs.append(df)
        # Concatenate all dataframes in the list into a single dataframe
        glofasDffinal = pd.concat(dfs, ignore_index=True)

        #glofasDffinal= pd.read_csv(self.glofasAdmnPerDay) 
        selectedPcodeVal=self.selectedPcode
        #glofasDffinal= glofasDffinal.query('pcode in @selectedPcode')
        
        df_district_mapping = pd.read_json(json.dumps(self.DISTRICT_MAPPING))
        df_thresholds = pd.read_json(json.dumps(self.GLOFAS_STATIONS))
        df_thresholds = df_thresholds.set_index("stationCode", drop=False)
        df_district_mapping = df_district_mapping.set_index("glofasStation", drop=False)
        
        stations=[]               
        for exractAdminCode in selectedPcodeVal:
            station = {}
            station['code'] = df_district_mapping.query('placeCode == @exractAdminCode')['glofasStation'].values[0]            
            if station['code'] in df_thresholds['stationCode']:#and station['code'] in df_district_mapping['glofasStation']:
                logger.info(station['code'])
                threshold = df_thresholds[df_thresholds['stationCode'] ==station['code']][TRIGGER_LEVEL][0]
            
                for step in range(1, 8):
                    # Loop through 51 ensembles, get forecast and compare to threshold
                    ensemble_options = 51
                    count = 0
                    dis_sum = 0
                    leadTimelabel=str(step)+'_day'   
                    
                    for ensemble in range(0, ensemble_options):
                        discharge = glofasDffinal.query('pcode in @exractAdminCode').query('ensemble ==@ensemble').query('leadTime ==@leadTimelabel')['dis'].values[0]
    
                        if discharge >= threshold:
                            count = count + 1
                        dis_sum = dis_sum + discharge                  

                    prob = int(count/ensemble_options)
                    dis_avg = dis_sum/ensemble_options
                    
                    station['fc'] = dis_avg
                    station['fc_prob'] = prob 
                    station['fc_trigger'] = 1 if prob > self.TRIGGER_LEVELS['minimum'] else 0
                    station['eapAlertClass'] = self.checkTriggerProb(prob)

                    if station['fc_trigger'] == 1:
                        trigger_per_day[str(step)+'-day'] = True

                    if step == self.leadTimeValue:
                        stations.append(station)
                    
        # Add 'no_station'
        for station_code in ['no_station']:
            station = {}
            station['code'] = station_code
            station['fc'] = 0
            station['fc_prob'] = 0
            station['fc_trigger'] = 0
            station['eapAlertClass'] = 'no'
            stations.append(station)

        with open(self.extractedGlofasPath, 'w') as fp:
            json.dump(stations, fp)
            logger.info('Extracted Glofas data - File saved_')

        with open(self.triggerPerDay, 'w') as fp:
            json.dump([trigger_per_day], fp)
            logger.info('Extracted Glofas data - Trigger per day File saved_')       
  
    
    def extractGlofasData(self):
        logger.info('\nExtracting Glofas (FTP) Data\n')

        files = [f for f in listdir(self.inputPath) if isfile(
            join(self.inputPath, f)) and f.endswith('.nc')]

        df_thresholds = pd.read_json(json.dumps(self.GLOFAS_STATIONS))
        df_thresholds = df_thresholds.set_index("stationCode", drop=False)
        df_district_mapping = pd.read_json(json.dumps(self.DISTRICT_MAPPING))
        df_district_mapping = df_district_mapping.set_index("glofasStation", drop=False)
        stations = []
        trigger_per_day = {
            '1-day': False,
            '2-day': False,
            '3-day': False,
            '4-day': False,
            '5-day': False,
            '6-day': False,
            '7-day': False,
        }

        # read glofas forecst data from a text file
        #
        if self.countryCodeISO3=='PHL':
            file_path= self.inputPath+'glofas_discharge_RedcrossPhilippines_'+ self.current_date+'00.txt'
            df = pd.read_csv(file_path, sep=r'\s+')  
            df[['stationCode', 'StationName']] = df['name'].str.split('_', expand=True)[[0,1]]
            #df.drop(columns=['name'], inplace=True)

            # read return period information from the text file

            file_path=self.inputPath+'glofas_returnlevels_ldd_ups_RedcrossPhilippines_'+ self.current_date+'00.txt'
            dfrp = pd.read_csv(file_path, sep=r'\s+')
            dfrp[['stationCode', 'StationName']] = dfrp['Name'].str.split('_', expand=True)[[0,1]]

            dffinal=pd.merge(df.copy(),dfrp.filter(['stationCode','lat','lon','2y','5y','20y','plat','plon']),  how='left',left_on='stationCode' , right_on ='stationCode')
            dffinal['time'] = pd.to_datetime(df['time'])
        else:
            file_path= self.inputPath+'glofas_discharge_ZambiaRedcross_'+ self.current_date+'00.txt'
            df = pd.read_csv(file_path, sep=r'\s+')  
            df[['stationCode', 'StationName']] = df['name'].str.split('_', expand=True)[[0,1]]
            #df.drop(columns=['name'], inplace=True)
            # read return period information from the text file
            file_path=self.inputPath+'glofas_returnlevels_ldd_ups_ZambiaRedcross_'+ self.current_date+'00.txt'
            dfrp = pd.read_csv(file_path, sep=r'\s+')
            dfrp[['stationCode', 'StationName']] = dfrp['Name'].str.split('_', expand=True)[[0,1]]
            dffinal=pd.merge(df.copy(),dfrp.filter(['stationCode','lat','lon','2y','5y','20y','plat','plon']),  how='left',left_on='stationCode' , right_on ='stationCode')
            dffinal['time'] = pd.to_datetime(df['time'])

        CURRENT_DATE_v=datetime.datetime.today()
        dffinal['LeadTime'] = dffinal.apply(lambda row: (row['time']-CURRENT_DATE_v).days,axis=1)

        for StCode in  list(set(df_thresholds.stationCode.values)):
            # Skip old stations > need to be removed from FTP
            logger.info(f'processing station: {StCode}')
            oldStations=['G5230',
                         'G1724',
                         'G5196',
                         'G2001',
                         'G5670',
                         'G5694']
            
            if StCode in ['no_station']:#oldStations:
                continue
                

            station = {}
            station['code'] = StCode 

            # Get threshold for this specific station
            if station['code'] in df_district_mapping['glofasStation']:
                
              
                threshold = df_thresholds[df_thresholds['stationCode'] ==station['code']][TRIGGER_LEVEL][0]

                # Set dimension-values
                time = 0

                for step in range(1, 8):

                    # Loop through 51 ensembles, get forecast and compare to threshold
      
                 

                    data = dffinal.query('stationCode==@StCode').query('LeadTime==@step')
                    if not data.empty:
                        #logger.info("Data frame not empty continue to process daily forecast ")
                        data['thresholdCheck'] = data['dis'].apply(lambda x: 1 if x > threshold else 0)       
                        result= data.agg({'dis' : ['sum', 'count'], 'thresholdCheck' : ['sum', 'count'],'member' : ['sum', 'count']})

                        dis_sum= list(result.dis.values)[0] #result['dis']                  
                        count= list(result.thresholdCheck.values)[0] #result['thresholdCheck']                  
                        ensemble_options=list(result.member.values)[1] #result['member']
                        #logger.info(f'data for processing station: {StCode} {dis_sum} {count} {ensemble_options}')
                        prob = int(count/ensemble_options)
                        dis_avg = dis_sum/ensemble_options

                        station['fc'] = dis_avg
                        station['fc_prob'] = prob 
                        station['fc_trigger'] = 1 if prob > self.TRIGGER_LEVELS['minimum'] else 0
                        station['eapAlertClass'] = self.checkTriggerProb(prob) # 1 if prob > self.eapAlertClass['min'] else 0

                        
                        #station['fc_trigger'] = 1 if prob > TRIGGER_LEVELS['minimum'] else 0
                        if station['fc_trigger'] == 1:
                            trigger_per_day[str(step)+'-day'] = True

                        if step == self.leadTimeValue:
                            stations.append(station)

                    station = {}
                    station['code'] = StCode

        # Add 'no_station'
        for station_code in ['no_station']:
            station = {}
            station['code'] = station_code
            station['fc'] = 0
            station['fc_prob'] = 0
            station['fc_trigger'] = 0
            station['eapAlertClass'] = 'no'
            stations.append(station)

        with open(self.extractedGlofasPath, 'w') as fp:
            json.dump(stations, fp)
            logger.info('Extracted Glofas data - File saved')

        with open(self.triggerPerDay, 'w') as fp:
            json.dump([trigger_per_day], fp)
            logger.info('Extracted Glofas data - Trigger per day File saved')


    def extractGlofasData_(self):
        logger.info('\nExtracting Glofas (FTP) Data\n')

        files = [f for f in listdir(self.inputPath) if isfile(
            join(self.inputPath, f)) and f.endswith('.nc')]

        df_thresholds = pd.read_json(json.dumps(self.GLOFAS_STATIONS))
        df_thresholds = df_thresholds.set_index("stationCode", drop=False)
        df_district_mapping = pd.read_json(json.dumps(self.DISTRICT_MAPPING))
        df_district_mapping = df_district_mapping.set_index("glofasStation", drop=False)
        stations = []
        trigger_per_day = {
            '1-day': False,
            '2-day': False,
            '3-day': False,
            '4-day': False,
            '5-day': False,
            '6-day': False,
            '7-day': False,
        }

        for i in range(0, len(files)):
            Filename = os.path.join(self.inputPath, files[i])
           
            # Skip old stations > need to be removed from FTP
            oldStations=['G5230_Na_ZambiaRedcross',
                         'glofas_discharge_G1724_X33',
                         'G5196_Uganda_Gauge',
                         'glofas_discharge_G2001_X34',
                         'glofas_discharge_G5670_X36',
                         'glofas_discharge_G5694_X37']
            
            if any(pattern in Filename for pattern in oldStations):
                continue
                
            #if 'G5230_Na_ZambiaRedcross' in Filename or 'G5196_Uganda_Gauge' in Filename:
            #    continue

            station = {}
            station['code'] = files[i].split('_')[2]

            data = xr.open_dataset(Filename)

            # Get threshold for this specific station
            if station['code'] in df_thresholds['stationCode'] and station['code'] in df_district_mapping['glofasStation']:
                
                logger.info(Filename)
                threshold = df_thresholds[df_thresholds['stationCode'] ==station['code']][TRIGGER_LEVEL][0]

                # Set dimension-values
                time = 0

                for step in range(1, 8):

                    # Loop through 51 ensembles, get forecast and compare to threshold
                    ensemble_options = 51
                    count = 0
                    dis_sum = 0
                    for ensemble in range(0, ensemble_options):

                        discharge = data['dis'].sel(
                            ensemble=ensemble, step=step).values[time][0]

                        if discharge >= threshold:
                            count = count + 1
                        dis_sum = dis_sum + discharge

                    prob = int(count/ensemble_options)
                    dis_avg = dis_sum/ensemble_options
                    station['fc'] = dis_avg
                    station['fc_prob'] = prob 
                    station['fc_trigger'] = 1 if prob > self.TRIGGER_LEVELS['minimum'] else 0
                    station['eapAlertClass'] = self.checkTriggerProb(prob) # 1 if prob > self.eapAlertClass['min'] else 0
                    
                    
                    #station['fc_trigger'] = 1 if prob > TRIGGER_LEVELS['minimum'] else 0
                    if station['fc_trigger'] == 1:
                        trigger_per_day[str(step)+'-day'] = True

                    if step == self.leadTimeValue:
                        stations.append(station)
                    station = {}
                    station['code'] = files[i].split(
                        '_')[2]

            data.close()

        # Add 'no_station'
        for station_code in ['no_station']:
            station = {}
            station['code'] = station_code
            station['fc'] = 0
            station['fc_prob'] = 0
            station['fc_trigger'] = 0
            station['eapAlertClass'] = 'no'
            stations.append(station)

        with open(self.extractedGlofasPath, 'w') as fp:
            json.dump(stations, fp)
            logger.info('Extracted Glofas data - File saved')

        with open(self.triggerPerDay, 'w') as fp:
            json.dump([trigger_per_day], fp)
            logger.info('Extracted Glofas data - Trigger per day File saved')

    def extractMockData(self):
        logger.info('\nExtracting Glofas (mock) Data\n')

        # Load input data
        df_thresholds = pd.read_json(json.dumps(self.GLOFAS_STATIONS))
        df_thresholds = df_thresholds.set_index("stationCode", drop=False)
        df_district_mapping = pd.read_json(json.dumps(self.DISTRICT_MAPPING))
        df_district_mapping = df_district_mapping.set_index("glofasStation", drop=False)

        # Set up variables to fill
        stations = []
        trigger_per_day = {
            '1-day': False,
            '2-day': False,
            '3-day': False,
            '4-day': False,
            '5-day': False,
            '6-day': False,
            '7-day': False,
        }
        

        for index, row in df_thresholds.iterrows():
            station = {}
            station['code'] = row['stationCode']
            station['eapAlertClass'] = 'no'

            if station['code'] in df_district_mapping['glofasStation'] and station['code'] != 'no_station':
                logger.info(station['code'])
                threshold = df_thresholds[df_thresholds['stationCode'] ==station['code']][TRIGGER_LEVEL][0]

                for step in range(1, 8):
                    # Loop through 51 ensembles, get forecast and compare to threshold
                    ensemble_options = 51
                    count = 0
                    dis_sum = 0

                    for ensemble in range(0, ensemble_options):
                        # MOCK OVERWRITE DEPENDING ON COUNTRY SETTING
                        if SETTINGS[self.countryCodeISO3]['if_mock_trigger'] == True:
                            if step < 3: # Only dummy trigger for 3-day and above
                                discharge = 0
                            elif station['code'] == 'G5220':  # UGA dummy flood station 1
                                discharge = 600
                            elif station['code'] == 'G1067':  # ETH dummy flood station 1
                                discharge = 5000
                            elif station['code'] == 'G1904':  # ETH dummy flood station 2
                                discharge = 5500
                            elif station['code'] == 'G5305':  # KEN dummy flood station
                                discharge = 3000
                            elif station['code'] == 'G5195':  # KEN dummy flood station 'G7195'
                                discharge = 500
                            elif station['code'] == 'G1361':  # ZMB dummy flood station 1
                                discharge = 8000
                            elif station['code'] == 'G1328':  # ZMB dummy flood station 2
                                discharge = 9000
                            elif station['code'] == 'G1319':  # ZMB dummy flood station 3
                                discharge = 1400
                            elif station['code'] == 'G5369':  # PHL dummy flood station 1 G1964 G1966 G1967
                                discharge = 7000
                            elif station['code'] == 'G4630':  # PHL dummy flood station 2
                                discharge = 19000
                            elif station['code'] == 'G196700':  # PHL dummy flood station 3
                                discharge = 11400
                            elif station['code'] == 'G5100':  # SS dummy flood station 3
                                discharge = 41400    
                            elif station['code'] == 'G1724':  # MWI dummy flood station 1
                                discharge = 10000
                            elif station['code'] == 'G2001':  # MWI dummy flood station 2
                                discharge = 11000
                            elif station['code'] == 'G5670':  # MWI dummy flood station 3
                                discharge = 5000
                            elif station['code'] == 'G5694':  # MWI dummy flood station 4
                                discharge = 46000
                            else:
                                discharge = 0
                        else:
                            discharge = 0

                        if discharge >= threshold:
                            count = count + 1
                        dis_sum = dis_sum + discharge

                    prob = int(count/ensemble_options)
                    dis_avg = dis_sum/ensemble_options
                    station['fc'] = dis_avg
                    station['fc_prob'] = prob
                    station['fc_trigger'] = 1 if prob > self.TRIGGER_LEVELS['minimum'] else 0
                    #station['fc_trigger'] = 1 if prob > TRIGGER_LEVELS['minimum'] else 0

                    if station['fc_trigger'] == 1:
                        trigger_per_day[str(step)+'-day'] = True
                        station['eapAlertClass'] = 'max'

                    if step == self.leadTimeValue:
                        stations.append(station)
                    station = {}
                    station['code'] = row['stationCode']


        # Add 'no_station'
        for station_code in ['no_station']:
            station = {}
            station['code'] = station_code
            station['fc'] = 0
            station['fc_prob'] = 0
            station['fc_trigger'] = 0
            station['eapAlertClass'] = 'no'
            stations.append(station)

        with open(self.extractedGlofasPath, 'w') as fp:
            json.dump(stations, fp)
            logger.info('Extracted Glofas data - File saved')

        with open(self.triggerPerDay, 'w') as fp:
            json.dump([trigger_per_day], fp)
            logger.info('Extracted Glofas data - Trigger per day File saved')

    def findTrigger(self):
        # Load (static) threshold values per station
        df_thresholds = pd.read_json(json.dumps(self.GLOFAS_STATIONS))
        df_thresholds = df_thresholds.set_index("stationCode", drop=False)
        df_thresholds.sort_index(inplace=True)
        # Load extracted Glofas discharge levels per station
        with open(self.extractedGlofasPath) as json_data:
            d = json.load(json_data)
        df_discharge = pd.DataFrame(d)
        df_discharge.index = df_discharge['code']
        df_discharge.sort_index(inplace=True)

        # Merge two datasets
        df = pd.merge(df_thresholds, df_discharge, left_index=True,
                      right_index=True)
        del df['lat']
        del df['lon']

        # Determine trigger + return period per water station
        for index, row in df.iterrows():
            fc = float(row['fc'])
            trigger = int(row['fc_trigger'])
            if trigger == 1:
                if (self.countryCodeISO3 == 'ZMB') or (self.countryCodeISO3 == 'MWI'):
                    if fc >= row['threshold20Year']:
                        return_period_flood_extent = 20
                    else:
                        return_period_flood_extent = 10
                else:
                    return_period_flood_extent = 25
            else:
                return_period_flood_extent = None
                
            if fc >= row['threshold20Year']:
                return_period = 20
            elif fc >= row['threshold10Year']:
                return_period = 10
            elif fc >= row['threshold5Year']:
                return_period = 5
            elif fc >= row['threshold2Year']:
                return_period = 2
            else:
                return_period = None
            
            df.at[index, 'fc_rp_flood_extent'] = return_period_flood_extent
            df.at[index, 'fc_rp'] = return_period

        out = df.to_json(orient='records')
        with open(self.triggersPerStationPath, 'w') as fp:
            fp.write(out)
            logger.info('Processed Glofas data - File saved')