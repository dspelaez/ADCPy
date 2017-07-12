# -*- coding: utf-8 -*-
"""
This code takes a raw netcdf file containing data from any 4 beam Janus 
acoustic doppler profiler, with or without a center beam, and transforms the
data into Earth coordinates.  Data are output to netCDF using controlled
vocabulary for the variable names, following the EPIC convention wherever
possible.  This means given along beam velocity for each 
beam are used to compute East, North, and two redundant vertical velocities.
The difference between the two vertical velocities can be considered as the
error velocity, as originally introduced for the RD Instruments workhorse ADCP.

Output is stored in a netcdf file structured according to PMEL EPIC conventions.

Depth dependent attributes are compute from the mean Pressure found in the raw
data file.  So it is best to have the time series trimmed to the in water
time or to provide the good ensemble indeces for in water time

Created on Tue May 16 13:33:31 2017

@author: mmartini
"""
import sys
import numpy as np 
#import netCDF4 as nc4
from netCDF4 import Dataset
import netCDF4 as netcdf
import datetime as dt
from datetime import datetime

def doEPIC_ADCPfile(cdfFile, ncFile, attFile, settings):
    
    # check some of the settings we can't live without
    if 'good_ensembles' not in settings.keys():
        settings['good_ensembles'] = [0, np.inf] # nothing from user, do them all
        print('No starting and ending ensembles specfied, processing entire file')
    if 'orientation' not in settings.keys():
        settings['orientation'] = "UP"
        settings['orientation_note'] = "assumed by program"
        print('No orientation specfied, assuming up-looking')
    else:
        settings['orientation_note'] = "user provided orientation"        
    if 'transducer_offset_from_bottom' not in settings.keys():
        settings['transducer_offset_from_bottom'] = 0
        print('No transducer_offset_from_bottom, assuming 0')
    if 'transformation' not in settings.keys():
        settings['transformation'] = "EARTH"

    rawcdf = Dataset(cdfFile, mode='r',format='NETCDF4')
    rawvars = []
    for key in rawcdf.variables.keys(): rawvars.append(key)
      
    # this function will operate on the files using the netCDF package
    nc = setupEPICnc(ncFile, rawcdf, attFile, settings)
    
    nbeams = nc.number_of_slant_beams #what if this isn't 4?
    nbins = len(rawcdf.dimensions['depth'])
    nens = len(rawcdf.dimensions['time'])
    ncvars = []
    for key in nc.variables.keys(): ncvars.append(key)

    # start and end indices
    s = settings['good_ensembles'][0]
    if settings['good_ensembles'][1] == np.inf:
        e = nens
    else:
        e = settings['good_ensembles'][1]
    print('Converting from index %d to %d of %s' % (s,e,cdfFile))

    # many variables do not need processing and can just be copied to the
    # new EPIC convention
    
    # raw variable name : EPIC variable name
    varlist = {'time':'time','sv':'SV_80'}
    
    for key in varlist:
        varobj = nc.variables[varlist[key]]
        varobj[:] = rawcdf.variables[key][s:e]  
    
    varlist = {'Hdg':'Hdg_1215','Ptch':'Ptch_1216','Roll':'Roll_1217',
               'Tx':'Tx_1211'}
    
    for key in varlist:
        varobj = nc.variables[varlist[key]]
        varobj[:] = rawcdf.variables[key][s:e]/100 # hundredths deg. to deg. 
        
    # nc.magnetic_variation_applied
    # nc.magnetic_variation_applied_note = "as stated by user, not provided by Veloity processing"
    # nc.magnetic_variation_at_site
        
    # TODO correct heading to True and store that instead
    
    if 'Pressure' in rawvars:
        # TODO - detect conversion by units depending on isntrument type
        # in this case, decapascals to dbar = /1000
        convconst = 1000
        nc['P_1'][:] = rawcdf.variables['Pressure'][s:e]/convconst
    
    if 'cor5' in rawvars:
        nc['corvert'][:] = rawcdf.variables['cor5'][s:e,:]
    
    if 'AGC5' in rawvars:
        nc['AGCvert'][:] = rawcdf.variables['AGC5'][s:e,:]

    nc['PGd_1203'][:,:,0,0] = rawcdf.variables['PGd4'][s:e,:]
    
    varobj = nc.variables['bindist']
    bindist = np.arange(len(nc['bindist']))
    bindist = bindist*nc.bin_size+nc.center_first_bin
    nc['bindist'][:] = bindist
    # TODO - write boolean up/down depending on data read, depending on instrument

    # figure out DELTA_T
    dtime = np.diff( nc['time'][:])
    DELTA_T = '%s' % int((dtime.mean().astype('float')).round())
    nc.DELTA_T = DELTA_T
    
    # depths and heights
    nc.initial_instrument_height = settings['transducer_offset_from_bottom']
    nc.initial_instrument_height_note = "height in meters above bottom: accurate for tripod mounted instruments" 
    # compute depth, make a guess we want to average all depths recorded 
    # deeper than user supplied water depth
    # idx is returned as a tuple, the first of which is the actual index values

    if 'Pressure' in rawvars:
        idx = np.where(nc['P_1'] > nc.WATER_DEPTH/2)
        # now for the mean of only on bottom pressure measurements
        pmean = nc['P_1'][idx[0]].mean()
        print('Site WATER_DEPTH given is %f' % nc.WATER_DEPTH)
        print('Calculated mean water level from P_1 is %f m' % pmean)
        print('Updating site WATER_DEPTH to %f m' % pmean)
        nc.WATER_DEPTH = pmean+nc.transducer_offset_from_bottom
        nc.WATER_DEPTH_source = "water depth = MSL from pressure sensor"
        nc.nominal_sensor_depth = pmean-settings['transducer_offset_from_bottom']
        nc.nominal_sensor_depth_note = "inst_depth = (water_depth - inst_height); nominal depth below surface, meters"
        varnames = ['P_1','bindist','depth']
        # WATER_DEPTH_datum is not used in this circumstance.
    else:
        print('Site WATER_DEPTH given is %f' % nc.WATER_DEPTH)
        print('No pressure data available, so no adjustment to water depth made')
        nc.nominal_sensor_depth = nc.WATER_DEPTH-settings['transducer_offset_from_bottom']
        nc.nominal_sensor_depth_note = "inst_depth = (water_depth - inst_height); nominal depth below surface, meters"
        varnames = ['bindist','depth']
        # WATER_DEPTH_datum is not used in this circumstance.

    for varname in varnames:
        nc[varname].WATER_DEPTH = nc.WATER_DEPTH
        nc[varname].WATER_DEPTH_source = nc.WATER_DEPTH_source
        nc[varname].transducer_offset_from_bottom = nc.transducer_offset_from_bottom
    
    # update depth variable for location of bins based on newly computed WATER_DEPTH
    if "UP" in nc.orientation:
        depths = nc.WATER_DEPTH-nc.transducer_offset_from_bottom-nc['bindist']
    else:
        depths = -1 * (nc.WATER_DEPTH-nc.transducer_offset_from_bottom+nc['bindist'])
    
    nc['depth'][:] = depths        
    
    nc.start_time = '%s' % netcdf.num2date(nc['time'][0],nc['time'].units)
    nc.stop_time = '%s' % netcdf.num2date(nc['time'][-1],nc['time'].units)
    
    # some of these repeating attributes depended on depth calculations
    # these are the same for all variables because all sensors are in the same
    # package, as of now, no remote sensors being logged by this ADCP
    ncvarnames = []
    for key in nc.variables.keys(): ncvarnames.append(key)
    omitnames = []
    for key in nc.dimensions.keys(): omitnames.append(key)
    omitnames.append("Rec")
    omitnames.append("depth")
    for varname in ncvarnames:
        if varname not in omitnames:
            varobj = nc.variables[varname]
            varobj.sensor_type = nc.sensor_type
            varobj.sensor_depth = nc.nominal_sensor_depth
            varobj.initial_sensor_height = nc.initial_instrument_height 
            varobj.initial_sensor_height_note = "height in meters above bottom:  accurate for tripod mounted instruments"
            varobj.height_depth_units = "m"

    print('finished copying data, starting computations at %s' % (dt.datetime.now()))

    print('averaging cor at %s' % (dt.datetime.now()))
    # this will be a problem - it loads all into memory
    cor = (rawcdf.variables['cor1'][s:e,:]+rawcdf.variables['cor2'][s:e,:]+ \
        rawcdf.variables['cor3'][s:e,:]+rawcdf.variables['cor4'][s:e,:]) / 4
    #varobj = nc.variables['cor']
    nc['cor'][:,:,0,0] = cor[:,:]

    print('averaging AGC at %s' % (dt.datetime.now()))
    # this will be a problem - it loads all into memory
    agc = (rawcdf.variables['AGC1'][s:e,:]+rawcdf.variables['AGC2'][s:e,:]+ \
        rawcdf.variables['AGC3'][s:e,:]+rawcdf.variables['AGC4'][s:e,:]) / 4
    #varobj = nc.variables['cor']
    nc['AGC_1202'][:,:,0,0] = agc[:,:]
    
    print('converting %d ensembles from beam to earth %s' % (len(nc['time']), dt.datetime.now()))
    
    if rawcdf.TRDI_Beam_Pattern == "Convex":
        convex = True
    else:
        convex = False
    
    # set up containers to operate on
    vbeam =  np.asmatrix(np.ones([nbeams,nbins], dtype=float, order='C')*np.NAN)
    vinst =  np.asmatrix(np.ones([nbeams,nbins], dtype=float, order='C')*np.NAN)
    #vxyz =  np.asmatrix(np.ones([3,nbins], dtype=float, order='C')*np.NAN)
    #vearth =  np.asmatrix(np.ones([nbeams,nbins], dtype=float, order='C')*np.NAN)
    vels = np.ones(nbins,dtype=float, order='C')*np.NAN
    
    
    # TODO replace this with xarray methodology when we understand how to write
    # xarray to netcdf
        
    # this beam arrangement is for TRDI Workhorse and V
    rawvarnames = ["vel1", "vel2", "vel3", "vel4"]
    ncidx = 0
    if settings['transformation'].upper() == "BEAM":
        ncvarnames = ["Beam1", "Beam2", "Beam3", "Beam4"]        
        for idx in range(s,e):
            for beam in range(nbeams):
                nc[ncvarnames[beam]][ncidx,:,0,0] = \
                    rawcdf.variables[rawvarnames[beam]][idx,:] /10
            ncidx = ncidx +1
    elif settings['transformation'].upper() == "INST":
        ncvarnames = ["X","Y","Z","Error"]            
        # get the beam to instrument rotation matrix
        Tb2i = calc_beam_rotmatrix(rawcdf.TRDI_Beam_Angle, convex)
        for idx in range(s,e):
            for beam in range(nbeams):
                vbeam[beam,:] = rawcdf.variables[rawvarnames[beam]][idx,:] /10
                vinst = Tb2i*vbeam
                #vels = np.array(vinst[beam,:])
                nc[ncvarnames[beam]][ncidx,:,0,0] = np.array(vinst[beam,:])
            ncidx = ncidx +1
    else:
        ncvarnames = ["u_1205","v_1206","w_1204","Werr_1201"]
        # get the beam to instrument rotation matrix
        Tb2i = calc_beam_rotmatrix(rawcdf.TRDI_Beam_Angle, convex)
        declination = nc.magnetic_variation_at_site
        nc.magnetic_variation_applied = declination
        for idx in range(s,e):
            heading = rawcdf.variables['Hdg'][idx] /100
            heading = heading + declination
            pitch = rawcdf.variables['Ptch'][idx] /100
            roll = rawcdf.variables['Roll'][idx] /100
            Mi2e = cal_earth_rotmatrix(heading,pitch,roll,declination)
            for beam in range(nbeams):
                vbeam[beam,:] = rawcdf.variables[rawvarnames[beam]][idx,:] /10             
            vinst = Tb2i*vbeam
            vearth = Mi2e*vinst[0:3,:]
            for beam in range(nbeams):
                if beam < 3:
                    vels = np.array(vearth[beam,:])
                else:
                    vels = np.array(vinst[beam,:])
                nc[ncvarnames[beam]][ncidx,:,0,0] = vels                    
            ncidx = ncidx +1
            
    # TODO minima and maxima here - or as a separate operation using xarray
    # in fact - using xarray will allow practice in xarray operations and 
    # writing to_netcdf output on existing netcdf files

    print('closing files at %s' % (dt.datetime.now()))

    rawcdf.close()
    nc.close()
   
    return

""" this transformation matrix is from the R.D. Instruments Coordinate 
    Transformation booklet.  It presumes the beams are in the same position as
    RDI Workhorse ADCP beams, where, when looking down on the transducers:
        Beam 3 is in the direction of the compass' zero reference
        Beam 1 is to the right
        Beam 2 is to the left
        Beam 4 is opposite beam 3
        Pitch is about the beam 2-1 axis and is positive when beam 3 is raised
        Roll is about the beam 3-4 axis and is positive when beam 2 is raised
        Heading increases when beam 3 is rotated towards beam 1
    Nortek Signature differs in these ways:
        TRDI beam 3 = Nortek beam 1
        TRDI beam 1 = Nortek beam 2
        TRDI beam 4 = Nortek beam 3
        TRDI beam 2 = Nortek beam 4
        Heading, pitch and roll behave the same as TRDI
"""
# this code thanks to https://github.com/lkilcher/dolfyn  rotate.py
def calc_beam_rotmatrix(theta=20, convex=True, degrees=True):
    """Calculate the rotation matrix from beam coordinates to
    instrument head coordinates.
    Parameters
    ----------
    theta : is the angle of the heads (usually 20 or 30 degrees)
    convex : is a flag for convex or concave head configuration.
    degrees : is a flag which specifies whether theta is in degrees
        or radians (default: degrees=True)
    """
    deg2rad = np.pi / 180.
    
    if degrees:
        theta = theta * deg2rad
    if convex == 0 or convex == -1:
        c = -1
    else:
        c = 1
    a = 1 / (2. * np.sin(theta))
    b = 1 / (4. * np.cos(theta))
    d = a / (2. ** 0.5)
    return np.asmatrix(np.array([[c * a, -c * a, 0, 0],
                     [0, 0, -c * a, c * a],
                     [b, b, b, b],
                     [d, d, -d, -d]]))

# as per the R.D. Instruments Coordinate Transformation booklet                     
def cal_earth_rotmatrix(heading=0,pitch=0,roll=0,declination=0):
    # for declination, West is negative
    # heading, pitch and roll are in degrees
    ch = np.cos(heading)
    sh = np.sin(heading)
    cp = np.cos(pitch)
    sp = np.sin(pitch)
    cr = np.cos(roll)
    sr = np.sin(roll)
    
    return np.asmatrix(np.array([[(ch*cr+sh*sp*sr), (sh*cp), (ch*sr-sh*sp*cr)],
                                [(-sh*cr+ch*sp*sr), (ch*cp), (-sh*sr-ch*sp*cr)],
                                [(-cp*sr), sp, (cp*cr)]]))

def setupEPICnc(fname, rawcdf, attfile, settings):
     
    # note that 
    # f4 = 4 byte, 32 bit float
    maxfloat = 3.402823*10**38;
    intfill = -32768
    floatfill = 1E35
    
    # check the ensemble limits asked for by the user
    nens = rawcdf.variables['Rec'].size
    if settings['good_ensembles'][1] == np.inf:
        settings['good_ensembles'][1] = nens
    if settings['good_ensembles'][0] < 0:
        settings['good_ensembles'][0] = 0
    if settings['good_ensembles'][1] > nens:
        settings['good_ensembles'][1] = nens-1
    nens2write = settings['good_ensembles'][1]-settings['good_ensembles'][0]
                 
    print('creating netCDF file %s with %d records' % (fname, nens2write))
    
    rawvars = []
    for key in rawcdf.variables.keys(): rawvars.append(key)
    rawgatts = []
    for key in rawcdf.variables.keys(): rawgatts.append(key)
    
    nbins = len(rawcdf.dimensions['depth'])
    
    cdf = Dataset(fname, "w", clobber=True, format="NETCDF4")
    
    # dimensions, in EPIC order
    cdf.createDimension('time',nens2write)
    cdf.createDimension('depth',nbins)
    cdf.createDimension('lat',1)
    cdf.createDimension('lon',1)
    
    # write global attributes
    cdf.history = rawcdf.history+"rotations calculated and converted to EPIC format by ADCPcdf2ncEPIC.py"
    
    # these get returned as a dictionary
    gatts = read_globalatts(attfile)
    
    if 'WATER_DEPTH' not in gatts.keys():
        gatts['WATER_DEPTH'] = 0 # nothing from user
        print('No WATER_DEPTH found, check depths of bins and WATER_DEPTH!')
    gatts['orientation'] = settings['orientation'].upper()
    
    if 'serial_number' not in gatts.keys():
        gatts['serial_number'] = "unknown"
    
    if 'magnetic_variation' not in gatts.keys():
        gatts['magnetic_variation_at_site'] = 0
        print('No magnetic_variation, assuming magnetic_variation_at_site = 0')
    else:
        gatts['magnetic_variation_at_site'] = gatts['magnetic_variation']
        gatts.pop('magnetic_variation')

    writeDict2atts(cdf, gatts, "")
    
    # more standard attributes
    cdf.latitude_units = "degree_north"
    cdf.longitude_units = "degree_east"
    cdf.CREATION_DATE = "%s" % datetime.now()
    cdf.DATA_TYPE = "ADCP"
    cdf.FILL_FLAG = 0
    cdf.COMPOSITE = 0
    
    # attributes that the names will vary depending on the ADCP vendor
    # TODO make a function template that feeds these to a dictionary depending
    # on whose ADCP this is
    if rawcdf.sensor_type == "TRDI":
        # TRDI attributes
        cdf.sensor_type = "TRDI"
        cdf.bin_size = rawcdf.TRDI_Depth_Cell_Length_cm/100
        cdf.bin_count = rawcdf.TRDI_Number_of_Cells
        cdf.center_first_bin = rawcdf.TRDI_Bin_1_distance_cm/100
        cdf.blanking_distance = rawcdf.TRDI_Blank_after_Transmit_cm/100
        cdf.transform = rawcdf.TRDI_Coord_Transform
        cdf.beam_angle = rawcdf.TRDI_Beam_Angle
        cdf.number_of_slant_beams = rawcdf.TRDI_Number_of_Beams
    
    # attributes requiring user input
    cdf.transducer_offset_from_bottom = settings['transducer_offset_from_bottom']
    cdf.initial_instrument_height = settings['transducer_offset_from_bottom']
    # TODO check on orientation, using user input for now
    #rawcdf.TRDI_Orientation 
    # need translation to UP from "Up-facing beams"
    cdf.orientation = settings['orientation'].upper()
    cdf.orientation_note = settings['orientation_note']
    if settings['orientation'].upper() == 'UP':
        cdf.depth_note = "uplooking bin depths = WATER_DEPTH-transducer_offset_from_bottom-bindist"
    else:
        cdf.depth_note = "downlooking bin depths = WATER_DEPTH-transducer_offset_from_bottom+bindist"
    cdf.serial_number = rawcdf.serial_number
    
      
    varobj = cdf.createVariable('Rec','u4',('time'),fill_value=intfill)
    varobj.units = "count"
    varobj.long_name = "Ensemble Number"
    # the ensemble number is a two byte LSB and a one byte MSB (for the rollover)
    varobj.valid_range = [0, 2**23]

    # if f8, 64 bit is not used, time is clipped
    # for ADCP fast sampled, single ping data, need millisecond resolution
    varobj = cdf.createVariable('time','f8',('time'))
    varobj.units = rawcdf.variables['time'].units
    # we are not using these EPIC definitions yet.  They are here for reference
    #varobj = cdf.createVariable('Rec','int',('time'))
    #varobj = cdf.createVariable('time','int',('Rec'))
    #varobj.units = "True Julian Day"
    #varobj.epic_code = 624
    #varobj.datum = "Time (UTC) in True Julian Days: 2440000 = 0000 h on May 23, 1968"
    #varobj.NOTE = "Decimal Julian day [days] = time [days] + ( time2 [msec] / 86400000 [msec/day] )"    
    #varobj = cdf.createVariable('time2','int',('Rec'))
    #varobj.units = "msec since 0:00 GMT"
    #varobj.epic_code = 624
    #varobj.datum = "Time (UTC) in True Julian Days: 2440000 = 0000 h on May 23, 1968"
    #varobj.NOTE = "Decimal Julian day [days] = time [days] + ( time2 [msec] / 86400000 [msec/day] )"    

    varobj = cdf.createVariable('depth','f4',('depth'),fill_value=floatfill)
    varobj.units = "m"
    varobj.long_name = "DEPTH (M)"
    varobj.epic_code = 3
    varobj.center_first_bin = cdf.center_first_bin
    varobj.blanking_distance = cdf.blanking_distance
    varobj.bin_size = cdf.bin_size
    varobj.bin_count = nbins
    varobj.transducer_offset_from_bottom = cdf.transducer_offset_from_bottom
    
    varobj = cdf.createVariable('lat','f4',('lat'),fill_value=floatfill)
    varobj.units = "degree_north"
    varobj.epic_code = 500
    # note name is one of the netcdf4 reserved attributes, use setncattr
    varobj.setncattr('name', "LAT")
    varobj.long_name = "LATITUDE"
    varobj.generic_name = "lat"
    varobj.datum = "NAD83"
    varobj[:] = float(gatts['latitude'])    

    varobj = cdf.createVariable('lon','f4',('lon'),fill_value=floatfill)
    varobj.units = "degree_east"
    varobj.epic_code = 502
    # note name is one of the netcdf4 reserved attributes, use setncattr
    varobj.setncattr('name', "LON")
    varobj.long_name = "LONGITUDE"
    varobj.generic_name = "lon"
    varobj.datum = "NAD83"
    varobj[:] = float(gatts['longitude'])    
    
    varobj = cdf.createVariable('bindist','f4',('depth'),fill_value=floatfill)
    # note name is one of the netcdf4 reserved attributes, use setncattr
    varobj.setncattr('name', "bindist")
    varobj.units = "m"
    varobj.long_name = "bin distance from instrument"
    varobj.epic_code = 0
    #varobj.valid_range = [0 0]
    varobj.center_first_bin = cdf.center_first_bin
    varobj.blanking_distance = cdf.blanking_distance
    varobj.bin_size = cdf.bin_size
    varobj.bin_count = nbins
    varobj.transducer_offset_from_bottom = cdf.transducer_offset_from_bottom
    varobj.NOTE = "distance is along profile from instrument head to center of bin"
    
    rawvarobj = rawcdf.variables['sv']
    varobj = cdf.createVariable('SV_80','f4',('time','lat','lon'),fill_value=floatfill)
    varobj.units = "m s-1"
    varobj.epic_code = 80
    # note name is one of the netcdf4 reserved attributes, use setncattr
    varobj.setncattr('name', "SV")
    varobj.long_name = "SOUND VELOCITY (M/S)"
    varobj.generic_name = " "
    varobj.valid_range = rawvarobj.valid_range
    
    rawvarobj = rawcdf.variables['Hdg']
    varobj = cdf.createVariable('Hdg_1215','f4',('time','lat','lon'),fill_value=floatfill)
    varobj.units = "degrees"
    # note name is one of the netcdf4 reserved attributes, use setncattr
    varobj.setncattr('name', "Hdg")
    varobj.long_name = "INST Heading"
    varobj.generic_name = "hdg"
    varobj.epic_code = 1215
    #varobj.heading_alignment = rawvarobj.heading_alignment
    #varobj.heading_bias = rawvarobj.heading_bias
    varobj.valid_range = rawvarobj.valid_range/100 # will depend on the instrument
    # TODO - this note is instrument specific
    #varobj.NOTE = rawvarobj.NOTE_9
    
    rawvarobj = rawcdf.variables['Ptch']
    varobj = cdf.createVariable('Ptch_1216','f4',('time','lat','lon'),fill_value=floatfill)
    varobj.units = "degrees"
    varobj.long_name = "INST Pitch"
    varobj.epic_code = 1216
    varobj.valid_range = rawvarobj.valid_range/100
    
    rawvarobj = rawcdf.variables['Roll']
    varobj = cdf.createVariable('Roll_1217','f4',('time','lat','lon'),fill_value=floatfill)
    varobj.units = "degrees"
    varobj.long_name = "INST Roll"
    varobj.epic_code = 1217
    varobj.valid_range = rawvarobj.valid_range/100
    
    rawvarobj = rawcdf.variables['Tx']
    varobj = cdf.createVariable('Tx_1211','f4',('time','lat','lon'),fill_value=floatfill)
    varobj.units = "C"
    # note name is one of the netcdf4 reserved attributes, use setncattr
    varobj.setncattr('name', "T")
    varobj.long_name = "instrument Transducer Temp."
    varobj.epic_code = 1211
    varobj.valid_range = rawvarobj.valid_range/100   

    if 'Pressure' in rawvars:
        rawvarobj = rawcdf.variables['Pressure']
        varobj = cdf.createVariable('P_1','f4',('time','lat','lon'),fill_value=floatfill)
        varobj.units = "dbar"
        # note name is one of the netcdf4 reserved attributes, use setncattr
        varobj.setncattr('name', "P")
        varobj.long_name = "PRESSURE (DB)"
        varobj.generic_name = "depth"
        varobj.epic_code = 1
        varobj.valid_range = [0, maxfloat]
    
    varobj = cdf.createVariable('Or','B',('time','lat','lon'),fill_value=intfill)
    varobj.units = "boolean"
    varobj.long_name = "ORIENTATION 1=UP, 0=DOWN"
    varobj.generic_name = "orientation"
    varobj.valid_range = [0, 1]

    # purposefully omitting battery voltage for now as it may vary by instrument
    
    # TODO - some ADCPs provide pressure variance, dow e want to include?

    varobj = cdf.createVariable('cor','u2',('time','depth','lat','lon'),fill_value=intfill)
    varobj.setncattr('name','cor')
    varobj.long_name = "Slant Beam Average Correlation (cor)"
    varobj.generic_name = "cor"
    varobj.units = "counts"
    varobj.epic_code = 1202
    varobj.valid_range = [0, 255]
    varobj.NOTE = "Calculated from the slant beams"

    varobj = cdf.createVariable('PGd_1203','u2',('time','depth','lat','lon'),fill_value=intfill)
    varobj.setncattr('name','Pgd')
    varobj.long_name = "Percent Good Pings"
    varobj.generic_name = "PGd"
    varobj.units = "percent"
    varobj.epic_code = 1203
    varobj.valid_range = [0, 100]
    varobj.NOTE = "Percentage of good 4-bem solutions (Field #4)"

    varobj = cdf.createVariable('AGC_1202','u2',('time','depth','lat','lon'),fill_value=intfill)
    varobj.setncattr('name','AGC')
    varobj.long_name = "Average Echo Intensity (AGC)"
    varobj.generic_name = "AGC"
    varobj.units = "counts"
    varobj.epic_code = 1202
    varobj.NOTE = "Calculated from the slant beams"
    varobj.valid_range = [0, 255]

    if 'cor5' in rawvars:
        varobj = cdf.createVariable('corvert','u2',('time','depth','lat','lon'),fill_value=intfill)
        varobj.setncattr('name','cor')
        varobj.long_name = "Vertical Beam Correlation (cor)"
        varobj.generic_name = "cor"
        varobj.units = "counts"
        varobj.epic_code = 1202
        varobj.valid_range = [0, 255]
        varobj.NOTE = "From the center vertical beam"

    if 'AGC5' in rawvars:
        varobj = cdf.createVariable('AGCvert','u2',('time','depth','lat','lon'),fill_value=intfill)
        varobj.setncattr('name','AGC')
        varobj.long_name = "Vertical Beam Echo Intensity (AGC)"
        varobj.generic_name = "AGCvert"
        varobj.units = "counts"
        varobj.epic_code = 1202
        varobj.valid_range = [0, 255]
        varobj.NOTE = "From the center vertical beam"
        
    # repeating attributes that do not depend on height or depth calculations
    cdfvarnames = []
    for key in cdf.variables.keys(): cdfvarnames.append(key)
    omitnames = []
    for key in cdf.dimensions.keys(): omitnames.append(key)
    omitnames.append("Rec")
    for varname in cdfvarnames:
        if varname not in omitnames:
            varobj = cdf.variables[varname]
            varobj.serial_number = cdf.serial_number

    if settings['transformation'].upper() == "BEAM":
        varnames = ["Beam1", "Beam2", "Beam3", "Beam4"]
        codes = [0,0,0,0]
    elif settings['transformation'].upper() == "INST":
        varnames = ["X","Y","Z","Error"]            
        codes = [0,0,0,0]
    else:
        varnames = ["u_1205","v_1206","w_1204","Werr_1201"]
        codes = [1205, 1206, 1204, 1201]
        
    for i in range(4):
        varname = varnames[i]
        varobj = cdf.createVariable(varname,'f4',('time','depth','lat','lon'),fill_value=floatfill)
        varobj.units = "cm s-1"
        varobj.long_name = "%s velocity (cm s-1)" % varnames[i]
        varobj.epic_code = codes[i]
        varobj.valid_range = [-1e35, 1e35]

    # TODO do we do bottom track data here?  Later?  Or as a separate thing?

    add_VAR_DESC(cdf)
    
    #cdf.close()
    
    return cdf


def add_VAR_DESC(cdf):
    # cdf is an netcdf file object (e.g. pointer to open netcdf file)

    varkeys = cdf.variables.keys() #get the names
    dimkeys = cdf.dimensions.keys()
    varnames = []
    for key in varkeys: varnames.append(key)
    dimnames = []
    for key in dimkeys: dimnames.append(key)
    buf = ""
    for varname in varnames:
        if varname not in dimnames:
            buf = "%s:%s" % (buf,varname)

    cdf.VAR_DESC = buf  
    
    return
    
    
def read_globalatts(fname):        
    # read_globalatts: read in file of metadata for a tripod or mooring
    # usage : gatt=read_globalatts(fname)
    #
    # reads global attributes for an experiment from a text file (fname)
    # called by all data processing programs to get uniform metadata input
    #  one argument is required- the name of the file to read- it should have
    #  this form:
    # SciPi;C. Sherwood
    # PROJECT; ONR
    # EXPERIMENT; RIPPLES DRI
    # DESCRIPTION; Stress, SSC, and Bedforms at MVCO 12-m fine/coarse transition site
    # DATA_SUBTYPE; MOORED
    # DATA_ORIGIN; USGS WHFS Sed Trans Group
    # COORD_SYSTEM; GEOGRAPHIC
    # Conventions; PMEL/EPIC
    # MOORING; 836
    # WATER_DEPTH; 10.99
    # latitude; 41.336063
    # longitude; -70.559615
    # magnetic_variation; -15
    # Deployment_date; 27-Aug-2007
    # Recovery_date;  ?
    gatts = {}
    f = open(fname,'r')
    for line in f:
        line = line.strip()
        cols = line.split(";")
        gatts[cols[0]] = cols[1].strip()
        
        
    f.close()
    return gatts
    
# write a dictionary to netCDF attributes
def writeDict2atts(cdfobj, d, tag):
    
    i = 0;
    # first, convert as many of the values in d to numbers as we can
    for key in iter(d):
        if type(d[key]) == str:
            try:
                d[key] = float(d[key])
            except ValueError:
                # we really don't need to print here, 
                # but python insists we do something
                #print('   can\'t convert %s to float' % key)
                i += 1

    for key in iter(d):
        newkey = tag + key
        try:
            cdfobj.setncattr(newkey,d[key])
        except:
            print('can\'t set %s attribute' % key)
    
    # return the dictionary it's numerized values 
    return d

    
def __main():
    # TODO add - and -- types of command line arguments
    print('%s running on python %s' % (sys.argv[0], sys.version))
	
    if len(sys.argv) < 2:
        print("%s useage:" % sys.argv[0])
        print("ADCPcdf2ncEPIC rawcdfname ncEPICname USGSattfile [startingensemble endingensemble]\n" )
        print("starting and ending ensemble are netcdf file indeces, NOT TRDI ensemble numbers")
        print("USGSattfile is a file containing EPIC metadata")
        sys.exit(1)
        
    try:
        infileName = sys.argv[1]
    except:
        print('error - input file name missing')
        sys.exit(1)
        
    try:
        outfileName = sys.argv[2]
    except:
        print('error - output file name missing')
        sys.exit(1)
        
    try:
        attfileName = sys.argv[3]
    except:
        print('error - global attribute file name missing')
        sys.exit(1)
        
    try:
        settings = sys.argv[4]
    except:
        print('error - settings missing - need dictionary of:')
        print('settings[\'trim_pressure_below\'] = 10000 # deca-pascals for TRDI ADCPs')
        print('settings[\'good_ensembles\'] = [0, np.inf] # use np.inf for all ensembles or omit')
        print('settings[\'transducer_offset_from_bottom\'] = 1 # m')
        print('settings[\'transformation\'] = "EARTH" # | BEAM | INST')
        sys.exit(1)

    # TODO - fix these so they are looking int he correct path
    """    
    # some input testing
    if ~os.path.isfile(infileName):
        print('error - input file not found')
        sys.exit(1)
        
    if ~os.path.isfile(attfileName):
        print('error - attribute file not found')
        sys.exit(1)
    """

    print('Converting %s to %s' % (infileName, outfileName))
    
    print('Start file conversion at ',dt.datetime.now())
    
    doEPIC_ADCPfile(infileName, outfileName, attfileName, settings)
    
    print('Finished file conversion at ',dt.datetime.now())


if __name__ == "__main__":
    __main()