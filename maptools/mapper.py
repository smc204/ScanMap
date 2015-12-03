# -*- coding: utf-8 -*-
"""
Created on Thu Mar 12 17:03:46 2015

@author: mittelberger
"""

import logging
import time
import os
import warnings
import numpy as np

with warnings.catch_warnings():
    warnings.simplefilter('ignore')
    from ViennaTools import ViennaTools as vt
    from ViennaTools import tifffile

from .autotune import Imaging, Tuning, DirtError
from . import autoalign


class Mapping(object):
    
    _corners = ['top-left', 'top-right', 'bottom-right', 'bottom-left']
    
    def __init__(self, **kwargs):
        self.superscan = kwargs.get('superscan')
        self.as2 = kwargs.get('as2')
        self.document_controller = kwargs.get('document_controller')
        self.coord_dict = kwargs.get('coord_dict')
        # frame_parameters are: rotation, imsize, impix, pixeltime
        self.frame_parameters = kwargs.get('frame_parameters', {})
        self.detectors = kwargs.get('detectors', {'HAADF': False, 'MAADF': True})
        # switches are: do_autotuning, use_z_drive, auto_offset, auto_rotation, compensate_stage_error,
        # acquire_overview, blank_beam
        self.switches = kwargs.get('switches', {})
        self.number_of_images = kwargs.get('number_of_images', 1)
        if kwargs.get('savepath') is not None:
            self._savepath = os.path.normpath(kwargs.get('savepath'))
        else:
            self._savepath = None
        self.event = kwargs.get('event')
        self.foldername = 'map_' + time.strftime('%Y_%m_%d_%H_%M')
        self.offset = kwargs.get('offset', 0)
        self._online = kwargs.get('online')
        self.autotuning_mode = kwargs.get('autotuning_mode', 'abort')
        
    @property
    def online(self):
        if self._online is None:
            if self.as2 is not None and self.superscan is not None:
                self._online = True
            else:
                logging.info('Going to offline mode because no instance of as2 and superscan was provided.')
                self._online = False
        return self._online

    @online.setter
    def online(self, online):
        self._online = online
        
    @property
    def savepath(self):
        return self._savepath
        
    @savepath.setter
    def savepath(self, savepath):
        self._savepath = os.path.normpath(savepath)
        
    def create_map_coordinates(self, compensate_stage_error=False):
        imsize = self.frame_parameters['fov']*1e-9
        distance = self.offset*imsize
        self.num_subframes = np.array((int(np.abs(self.rightX-self.leftX)/(imsize+distance))+1, 
                                       int(np.abs(self.topY-self.botY)/(imsize+distance))+1))
    
        map_coords = []
        frame_number = []
        
    
        # add additional lines and frames to number of subframes
        if compensate_stage_error:
            extra_lines = 2  # Number of lines to add at the beginning of the map
            extra_frames = 5  # Number of extra moves at each end of a line
            oldm = 0.25  # odd line distance mover (additional offset of odd lines, in fractions of (imsize+distance))
            eldm = 0  # even line distance mover (additional offset of even lines, in fractions of (imsize+distance))
            num_subframes = self.num_subframes + np.array((2*extra_frames, extra_lines))
            leftX = self.leftX - extra_frames*(imsize+distance)
            for i in range(extra_lines):
                if i % 2 == 0:  # Odd lines (have even indices because numbering starts with 0), e.g. map from left to right
                    topY = self.topY + imsize + oldm*distance
                else:
                    topY = self.topY + imsize + eldm*distance
        else:
            num_subframes = self.num_subframes
            leftX = self.leftX
            topY = self.topY
            oldm = 0
            eldm = 0
        # make a list of coordinates where images will be aquired.
        # Starting point is the upper-left corner and mapping will proceed to the right. The next line will start
        # at the right and scan towards the left. The next line will again start at the left, and so on. E.g. a "snake shaped"
        # path is chosen for the mapping.
    
        for j in range(num_subframes[1]):
            for i in range(num_subframes[0]):
                if j % 2 == 0:  # Odd lines (have even indices because numbering starts with 0), e.g. map from left to right
                    map_coords.append(tuple((leftX+i*(imsize+distance), 
                                             topY-j*(imsize+distance) - oldm*(imsize+distance))) +
                                      tuple(self.interpolation((leftX + i*(imsize+distance),
                                            topY-j*(imsize+distance) - oldm*(imsize+distance)))))
    
                    # Apply correct (continuous) frame numbering for all cases. If no extra positions are added, just 
                    # append the correct frame number. Elsewise append the correct frame number if a non-additional
                    # one, else None
                    if not compensate_stage_error:
                        frame_number.append(j*num_subframes[0]+i)
                    elif extra_frames <= i < num_subframes[0]-extra_frames and j >= extra_lines:
                        frame_number.append((j-extra_lines)*(num_subframes[0]-2*extra_frames)+(i-extra_frames))
                    else:
                        frame_number.append(None)
    
                else: # Even lines, e.g. scan from right to left
                    map_coords.append(tuple((leftX + (num_subframes[0] - (i+1))*(imsize + distance),
                                             topY-j*(imsize + distance) - eldm*(imsize + distance))) +
                                      tuple(self.interpolation(
                                            (leftX + (num_subframes[0] - (i+1))*(imsize + distance),
                                             topY-j*(imsize + distance) - eldm*(imsize + distance)))))
    
                    # Apply correct (continuous) frame numbering for all cases. If no extra positions are added, just 
                    # append the correct frame number. Elsewise append the correct frame number if a non-additional
                    # one, else None
                    if not compensate_stage_error:
                        frame_number.append(j*num_subframes[0]+(num_subframes[0]-(i+1)))
                    elif extra_frames <= i < num_subframes[0]-extra_frames and j >= extra_lines:
                        frame_number.append( (j-extra_lines)*(num_subframes[0]-2*extra_frames) +
                                             ((num_subframes[0]-2*extra_frames)-(i-extra_frames+1)) )
                    else:
                        frame_number.append(None)
    
        return (map_coords, frame_number)
    
    def handle_autotuning(self, number_frame, Tuner):
        # tests in each frame after aquisition if all 6 reflections in the fft are still there (only for frames where 
        # less than 50% of the area are covered with dirt). If not all reflections are visible, autofocus is applied
        # and the result is added as offset to the interpolated focus values. The dirt coverage is calculated by
        # considering all pixels intensities that are higher than 0.02 as dirt
#        Tuner = Tuning(event=self.event, document_controller=self.document_controller, as2=self.as2, 
#                       superscan=self.superscan)        
        message = ''
        Tuner.image = Tuner.image_grabber(frame_parameters=self.frame_parameters)
        #name = str('%.4d_%.3f_%.3f.tif' % (frame_number[counter-1],stagex*1e6,stagey*1e6))
        Tuner.dirt_mask = Tuner.dirt_detector()
        #calculate the fraction of 'bad' pixels and save frame if fraction is >0.5, but add note to "bad_frames" file
        if np.sum(Tuner.dirt_mask)/(np.shape(Tuner.image)[0]*np.shape(Tuner.image)[1]) > 0.5:
            message += 'Over 50% dirt coverage. '
            Tuner.logwrite('Over 50% dirt coverage in No. ' + number_frame)
        else:
            try:
                first_order, second_order = Tuner.find_peaks(imsize=self.frame_parameters['fov'],
                                                             second_order=True)
                number_peaks = np.count_nonzero(first_order[:,-1])+np.count_nonzero(second_order[:,-1])
            except RuntimeError:
                first_order = second_order = 0
                number_peaks = 0
    
            if number_peaks == 12:
                missing_peaks = 0
            elif number_peaks < 10:
                message += 'Missing '+str(12 - number_peaks)+' peaks. '
                Tuner.logwrite('No. '+str(number_frame) + ': Missing ' + str(12 - number_peaks) + ' peaks.')
                missing_peaks += 12 - number_peaks
    
            if missing_peaks > 12:
                Tuner.logwrite('No. '+str(number_frame) + ': Retune because '+str(missing_peaks) +
                               ' peaks miss in total.')
                message += 'Retune because '+str(missing_peaks)+' peaks miss in total. '
                
                # find place in the image with least dirt to do tuning there
                clean_spot, size = Tuner.find_biggest_clean_spot()
                clean_spot_nm = clean_spot * self.frame_parameters['fov'] / self.frame_parameters['size_pixels']
                tune_frame_parameters = {'size_pixels': (512, 512), 'pixeltime': 8, 'fov': 4,
                                         'rotation': 0, 'center': clean_spot_nm}
                try:
                    Tuner.kill_aberrations(frame_parameters=tune_frame_parameters)
                    if self.event is not None and self.event.is_set():
                        return (data, message)
                except DirtError:
                    Tuner.logwrite('No. '+str(number_frame) + ': Tuning was aborted because of dirt coming in.')
                    message += 'Tuning was aborted because of dirt coming in. '
                else:
                    Tuner.logwrite('No. '+str(number_frame) + ': New tuning: '+str(Tuner.aberrations))
                    message += 'New tuning: '+ str(Tuner.aberrations) + '. '
    
                    data_new = Tuner.image_grabber(frame_parameters = self.frame_parameters)
                    try:
                        first_order_new, second_order_new = Tuner.find_peaks(iamge=data_new,
                                                                             imsize=self.frame_parameters['fov'],
                                                                             second_order=True)
                        number_peaks_new = np.count_nonzero(first_order_new[:,-1]) + \
                                           np.count_nonzero(second_order_new[:,-1])
                    except RuntimeError:
                        message += 'Dismissed result because it did not improve tuning: ' + \
                                   str(Tuner.aberrations) + '. '
                        Tuner.logwrite('No. '+str(number_frame) + \
                                       ': Dismissed result because it did not improve tuning: ' + \
                                       str(Tuner.aberrations))
                        #reset aberrations to values before tuning
                        Tuner.image_grabber(acquire_image=False, relative_aberrations=False,
                                            aberrations=Tuner.aberrations_tracklist[0])
                        missing_peaks = 0
                    
                    else:
                        if number_peaks_new > number_peaks:
                            data = data_new
                            #add new focus as offset to all coordinates
                            for i in range(len(self.map_coords)):
                                temp_coord = np.array(self.map_coords[i])
                                temp_coord[i][3] += Tuner.aberrations['EHTFocus'] * 1e-9
                                self.map_coords[i] = tuple(temp_coord[i])
                            missing_peaks = 0
                        else:
                            message += 'Dismissed result because it did not improve tuning: ' + \
                                       str(Tuner.aberrations) + '. '
                            Tuner.logwrite('No. ' + str(number_frame) + \
                                           ': Dismissed result because it did not improve tuning: ' + \
                                           str(Tuner.aberrations))
                            #reset aberrations to values before tuning
                            Tuner.image_grabber(acquire_image=False, relative_aberrations=False,
                                            aberrations=Tuner.aberrations_tracklist[0])
                            missing_peaks=0
        
        return (data, message)

    def interpolation(self, target):
        """
        Bilinear Interpolation between 4 points that do not have to lie on a regular grid.
    
        Parameters
        -----------
        target : Tuple
            (x,y) coordinates of the point you want to interpolate
    
        points : List of tuples
            Defines the corners of a quadrilateral. The points in the list
            are supposed to have the order (top-left, top-right, bottom-right, bottom-left)
            The length of the tuples has to be at least 3 and can be as long as necessary.
            The output will always have the length (points[i] - 2).
    
        Returns
        -------
        interpolated_point : Tuple
            Tuple with the length (points[i] - 2) that contains the interpolated value(s) at target (i is
            a number iterating over the list entries).
        """
        result = tuple()
        points = []        

        for corner in self._corners:
            points.append(self.coord_dict[corner])    
        # Bilinear interpolation within 4 points that are not lying on a regular grid.
        m = (target[0]-points[0][0]) / (points[1][0]-points[0][0])
        n = (target[0]-points[3][0]) / (points[2][0]-points[3][0])
    
        Q1 = np.array(points[0]) + m*(np.array(points[1])-np.array(points[0]))
        Q2 = np.array(points[3]) + n*(np.array(points[2])-np.array(points[3]))
    
        l = (target[1]-Q1[1]) / (Q2[1]-Q1[1])
    
        T = Q1 + l*(Q2-Q1)
    
        for j in range(len(points[0])-2):
            result += (T[j+2],)
    
    #Interpolation with Inverse distance weighting with a Thin-PLate-Spline Kernel
    #    for j in range(len(points[0])-2):
    #        interpolated = 0
    #        sum_weights = 0
    #        for i in range(len(points)):
    #            distance = np.sqrt((target[0]-points[i][0])**2 + (target[1]-points[i][1])**2)
    #            weight = 1/(distance**2*np.log(distance))
    #            interpolated += weight * points[i][j+2]
    #            sum_weights += weight
    #        result += (interpolated/sum_weights,)
        return result
                
    def load_mapping_config(self, path):
        #config_file = open(os.path.normpath(path))
        #counter = 0
        # make sure function is not caught in an endless loop if 'end' is missing at the end
        #while counter < 1e3:
        with open(os.path.normpath(path)) as config_file:
            for line in config_file:
            #counter += 1
            #line = config_file.readline().strip()
                line = line.strip()
            
            #if line == 'end':
            #    break
                if line.startswith('#'):
                    continue
                elif line.startswith('{'):
                    line = line[1:].strip()
                    self.fill_dicts(line, config_file)
                elif len(line.split(':')) == 2:
                    line = line.split(':')
                    if hasattr(self, line[0].strip()):
                        setattr(self, line[0].strip(), eval(line[1].strip()))
                    continue
                else:
                    continue
        
        #config_file.close()
            
    def fill_dicts(self, line, file):
        if hasattr(self, line):
            if getattr(self, line) is None:
                setattr(self, line, {})
            counter = 0
            # Make sure function is not caught in an endless loop if '}' is missing
            while counter < 100:
                counter += 1
                subline = file.readline().strip()
                if subline.startswith('}'):
                    break
                elif subline.startswith('#'):
                    continue
                elif subline.endswith('}'):
                    subline = subline[:-1]
                    subline = subline.split(':')
                    getattr(self, line)[subline[0].strip()] = eval(subline[1].strip())
                    break
                else:
                    subline = subline.split(':')
                    getattr(self, line)[subline[0].strip()] = eval(subline[1].strip())

    def save_mapping_config(self, path=None):
        if path is None:
            path = os.path.join(self.savepath, self.foldername)
        if not os.path.exists(path):
            os.makedirs(path)
        path = os.path.join(path, 'configs_map.txt')
        
        #config_file = open(path, mode='w+')
        with open(path, mode='w+') as config_file:
        
            config_file.write('# Configurations for ' + self.foldername + '.\n')
            config_file.write('# This file can be loaded to resume the mapping process with the exact same parameters.\n')
            config_file.write('# Only edit this file if you know what you do. ')
            config_file.write('Otherwise the loading process can fail.\n\n')
            config_file.write('{ switches\n')
            for key, value in self.switches.items():
                config_file.write('\t' + str(key) + ': ' + str(value) + '\n')
            config_file.write('}\n\n{ detectors\n')
            for key, value in self.detectors.items():
                config_file.write('\t' + str(key) + ': ' + str(value) + '\n')
            config_file.write('}\n\n{ coord_dict\n')
            for key, value in self.coord_dict.items():
                config_file.write('\t' + str(key) + ': ' + str(value) + '\n')
            config_file.write('}\n\n{ frame_parameters\n')
            for key, value in self.frame_parameters.items():
                config_file.write('\t' + str(key) + ': ' + str(value) + '\n')
            config_file.write('}\n')
            config_file.write('\n# Other parameters\n')
            config_file.write('savepath: ' + repr(self.savepath) + '\n')
    #        config_file.write('foldername: ' + repr(self.foldername) + '\n')
            config_file.write('number_of_images: ' + str(self.number_of_images) + '\n')
            config_file.write('offset: ' + str(self.offset) + '\n')
            #config_file.write('\nend')
        
        #config_file.close()

    def sort_quadrangle(self):
        """
        Brings 4 points in the correct order to form a quadrangle.
    
        Parameters
        ----------
        coord_dict : dictionary
            4 points that are supposed to form a quadrangle.
    
        Returns
        ------
        coord_dict_sorted : dictionary
            Keys are called 'top-left', 'top-right', 'bottom-right' and 'bottom-left'. They contain the respective input tuple.
            Axis of the result are in standard directions, e.g. x points to the right and y to the top.
        """
        result = {}
        points = []        

        for corner in self._corners:
            points.append(self.coord_dict[corner])

        points.sort()
        
        if points[0][1] > points[1][1]:
            result['top-left'] = points[0]
            result['bottom-left'] = points[1]
        elif points[0][1] < points[1][1]:
            result['top-left'] = points[1]
            result['bottom-left'] = points[0]
    
        if points[2][1] > points[3][1]:
            result['top-right'] = points[2]
            result['bottom-right'] = points[3]
        elif points[2][1] < points[3][1]:
            result['top-right'] = points[3]
            result['bottom-right'] = points[2]
    
        return result
        
    def SuperScan_mapping(self, **kwargs):
        """
            This function will take a series of STEM images (subframes) to map a large rectangular sample area.
            coord_dict is a dictionary that has to consist of at least 4 tuples that hold stage coordinates in x,y,z - direction
            and the fine focus value, e.g. the tuples have to have the form: (x,y,z,focus). The keys for the coordinates of the 4 corners
            have to be called 'top-left', 'top-right', 'bottom-right' and 'bottom-left'.
            The user has to set the correct focus in all 4 corners. The function will then adjust the focus continuosly during mapping.
            Optionally, automatic focus adjustment is applied (default = off).
            Files will be saved to disk in a folder specified by the user. For each map a subfolder with current date and time is created.
    
        """
        # check for entries in kwargs that override instance variables
        if kwargs.get('event') is not None:
            self.event = kwargs.get('event')
        if kwargs.get('switches') is not None:
            self.switches = kwargs.get('switches')
        if kwargs.get('frame_parameters') is not None:
            self.frame_parameters = kwargs.get('frame_parameters')
        if kwargs.get('detectors') is not None:
            self.detectors = kwargs.get('detectors')
        if kwargs.get('coord_dict') is not None:
            self.coord_dict = kwargs.get('coord_dict')
        if kwargs.get('offset') is not None:
            self.offset = kwargs.get('offset')
        if kwargs.get('number_of_images') is not None:
            self.number_of_images = kwargs.get('number_of_images')
        if kwargs.get('savepath') is not None:
            self.savepath = kwargs.get('savepath')
        if kwargs.get('foldername') is not None:
            self.foldername = kwargs.get('foldername')
    
        if np.size(self.frame_parameters.get('pixeltime')) > 1 and \
           np.size(self.frame_parameters.get('pixeltime')) != self.number_of_images:
            raise ValueError('The number of given pixeltimes does not match the given number of frames that should ' +
                             'be recorded per location. You can either input one number or a list with a matching ' + 
                             'length.')
        pixeltimes = None
        if np.size(self.frame_parameters.get('pixeltime')) > 1:
            pixeltimes = self.frame_parameters.get('pixeltime')
            self.frame_parameters['pixeltime'] = pixeltimes[0]
                             
        self.save_mapping_config()
        
        if self.switches.get('do_autotuning'):
            img = Tuning(frame_parameters=self.frame_parameters.copy(), detectors=self.detectors, event=self.event,
                         online=self.online, document_controller=self.document_controller, as2=self.as2, 
                         superscan=self.superscan)
        else:            
            img = Imaging(frame_parameters=self.frame_parameters.copy(), detectors=self.detectors, online=self.online,
                          as2=self.as2, superscan=self.superscan, document_controller=self.document_controller)

        if self.number_of_images > 1 and self.switches['do_autotuning'] == True:
            img.logwrite('Acquiring an image series and using autofocus is currently not possible. ' +
                         'Autofocus will be disabled.', level='warn')
            self.switches['do_autotuning'] = False
            
        # Sort coordinates in case they were not in the right order
        self.coord_dict = self.sort_quadrangle()    
        # Find bounding rectangle of the four points given by the user        
        self.leftX = np.amin((self.coord_dict['top-left'][0], self.coord_dict['bottom-left'][0]))
        self.rightX = np.amax((self.coord_dict['top-right'][0], self.coord_dict['bottom-right'][0]))
        self.topY = np.amax((self.coord_dict['top-left'][1], self.coord_dict['top-right'][1]))
        self.botY = np.amin((self.coord_dict['bottom-left'][1], self.coord_dict['bottom-right'][1]))

        # calculate the number of subframes in (x,y). A small distance is kept between the subframes
        # to ensure they do not overlap
        map_coords, frame_number = self.create_map_coordinates(compensate_stage_error=
                                                               self.switches['compensate_stage_error'])
        # create output folder:
        self.store = os.path.join(self.savepath, self.foldername)
        if not os.path.exists(self.store):
            os.makedirs(self.store)
    
        test_map = []
        counter = 0
        self.missing_peaks = 0
        bad_frames = {}
        
        self.write_map_info_file()
        # Now go to each position in "map_coords" and take a snapshot    
        for frame_coord in map_coords:
            if self.event is not None and self.event.is_set():
                break
            counter += 1
            stagex, stagey, stagez, fine_focus = frame_coord
            img.logwrite(str(counter) + '/' + str(len(map_coords)) + ': (No. ' +
                         str(frame_number[counter-1]) + ') x: ' +str((stagex)) + ', y: ' + str((stagey)) + ', z: ' +
                         str((stagez)) + ', focus: ' + str((fine_focus)))
            # only do hardware operations when online
            if self.online:    
                if self.switches.get('blank_beam'):
                    self.as2.set_property_as_float('C_Blank', 1)
    
                vt.as2_set_control(self.as2, 'StageOutX', stagex)
                vt.as2_set_control(self.as2, 'StageOutY', stagey)
                if self.switches['use_z_drive']:
                    vt.as2_set_control(self.as2, 'StageOutZ', stagez)
                vt.as2_set_control(self.as2, 'EHTFocus', fine_focus)
    
                # Wait until movement of stage is done (wait longer time before first frame)
                if counter == 1:
                    time.sleep(10) # time in seconds
                elif frame_number[counter-1] is None:
                    time.sleep(1)
                else:
                    time.sleep(2)
    
                if frame_number[counter-1] is not None:
                    name = str('%.4d_%.3f_%.3f.tif' % (frame_number[counter-1], stagex*1e6, stagey*1e6))
                    
                    if self.switches.get('do_autotuning'):
                        if self.switches.get('blank_beam'):
                                self.as2.set_property_as_float('C_Blank', 0)
                                #time.sleep(0.7)
                                while not self.as2.get_property_as_float('C_Blank') == 0:
                                    print('Waiting for beam to be unblanked...')
                                    time.sleep(0.02)

                        data, message = self.handle_autotuning(frame_number[counter-1], img)

                        if self.switches.get('blank_beam'):
                                self.as2.set_property_as_float('C_Blank', 1)
                    else:
                        # Take frame and save it to disk
                        if self.number_of_images < 2:
                            if self.switches.get('blank_beam'):
                                self.as2.set_property_as_float('C_Blank', 0)
                                #time.sleep(0.5)
                                while not self.as2.get_property_as_float('C_Blank') == 0:
                                    print('Waiting for beam to be unblanked...')
                                    time.sleep(0.02)
                                    
                            data = img.image_grabber()
                            if self.switches.get('blank_beam'):
                                self.as2.set_property_as_float('C_Blank', 1)
    
                            tifffile.imsave(os.path.join(self.store, name), data)
                        else:
                            if self.switches.get('blank_beam'):
                                self.as2.set_property_as_float('C_Blank', 0)
                                #time.sleep(0.5)
                                while not self.as2.get_property_as_float('C_Blank') == 0:
                                    print('Waiting for beam to be unblanked...')
                                    time.sleep(0.02)
                                
                            for i in range(self.number_of_images):
                                if pixeltimes is not None:
                                    self.frame_parameters['pixeltime'] = pixeltimes[i]
                                data = img.image_grabber(frame_parameters=self.frame_parameters)
                                tifffile.imsave(os.path.join(self.store, name), data)
                            if self.switches.get('blank_beam'):
                                self.as2.set_property_as_float('C_Blank', 1)
                    
                    test_map.append(frame_coord)    
            else:
                if frame_number[counter-1] is not None:
                    test_map.append(frame_coord)
    
        if self.switches['do_autotuning']:
            bad_frames_file = open(self.store+'bad_frames.txt', 'w')
            bad_frames_file.write('#This file contains the filenames of \"bad\" frames and the cause for the listing.\n\n')
            for key, value in bad_frames.items():
                bad_frames_file.write('{0:30}{1:}\n'.format(key+':', value))
                
        if self.switches.get('blank_beam'):
            self.as2.set_property_as_float('C_Blank', 0)
    
        #acquire overview image if desired
        if self.online and self.switches['acquire_overview']:
            #Use longest edge as image size
            if abs(self.rightX-self.leftX) < abs(self.topY-self.botY):
                over_size = abs(self.topY-self.botY)*1.2e9
            else:
                over_size = abs(self.rightX-self.leftX)*1.2e9
    
            #Find center of mapped area:
            map_center = ((self.leftX+self.rightX)/2, (self.topY+self.botY)/2)
            #Goto center
            vt.as2_set_control(self.as2, 'StageOutX', map_center[0])
            vt.as2_set_control(self.as2, 'StageOutY', map_center[1])
            time.sleep(5)
            #acquire image and save it
            overview_parameters = {'size_pixels': (4096, 4096), 'center': (0,0), 'pixeltime': 4, \
                                'fov': over_size, 'rotation': self.frame_parameters['rotation']}
            image = img.image_grabber(frame_parameters=overview_parameters)
            tifffile.imsave(os.path.join(self.store, 'Overview_{:.0f}_nm.tif'.format(over_size)), image)
    
        if self.event is None or not self.event.is_set():
            x_map = np.zeros((self.num_subframes[1], self.num_subframes[0]))
            y_map = np.zeros((self.num_subframes[1], self.num_subframes[0]))
            z_map = np.zeros((self.num_subframes[1], self.num_subframes[0]))
            focus_map = np.zeros((self.num_subframes[1], self.num_subframes[0]))
            for j in range(self.num_subframes[1]):
                for i in range(self.num_subframes[0]):
                    if j%2 == 0: #Odd lines, e.g. map from left to right
                        x_map[j,i] = test_map[i+j*self.num_subframes[0]][0]
                        y_map[j,i] = test_map[i+j*self.num_subframes[0]][1]
                        z_map[j,i] = test_map[i+j*self.num_subframes[0]][2]
                        focus_map[j,i] = test_map[i+j*self.num_subframes[0]][3]
                    else: #Even lines, e.g. scan from right to left
                        x_map[j,(self.num_subframes[0]-(i+1))] = test_map[i+j*self.num_subframes[0]][0]
                        y_map[j,(self.num_subframes[0]-(i+1))] = test_map[i+j*self.num_subframes[0]][1]
                        z_map[j,(self.num_subframes[0]-(i+1))] = test_map[i+j*self.num_subframes[0]][2]
                        focus_map[j,(self.num_subframes[0]-(i+1))] = test_map[i+j*self.num_subframes[0]][3]
    
    
            tifffile.imsave(os.path.join(self.store, 'x_map.tif'), np.asarray(x_map, dtype='float32'))
            tifffile.imsave(os.path.join(self.store, 'y_map.tif'), np.asarray(y_map, dtype='float32'))
            tifffile.imsave(os.path.join(self.store, 'z_map.tif'), np.asarray(z_map, dtype='float32'))
            tifffile.imsave(os.path.join(self.store, 'focus_map.tif'), np.asarray(focus_map, dtype='float32'))
        
        self.superscan.stop_playing()
        img.logwrite('Done.\n')

    def write_map_info_file(self):
    
            def translator(switch_state):
                if switch_state:
                    return 'ON'
                else:
                    return 'OFF'
        
            config_file = open(os.path.join(self.store, 'map_info.txt'), 'w')
            config_file.write('#This file contains all parameters used for the mapping.\n\n')
            config_file.write('#Map parameters:\n')
            map_paras = {'Autofocus': translator(self.switches.get('do_autotuning')),
                         'Auto Rotation': translator(self.switches.get('auto_rotation')),
                         'Auto Offset': translator(self.switches.get('auto_offset')),
                         'Z Drive': translator(self.switches.get('use_z_drive')),
                         'Acquire_Overview': translator(self.switches.get('acquire_overview')),
                         'Number of frames': str(self.num_subframes[0])+'x'+str(self.num_subframes[1]),
                         'Compensate stage error': translator(self.switches.get('compensate_stage_error'))}
            for key, value in map_paras.items():
                config_file.write('{0:18}{1:}\n'.format(key+':', value))
            
            config_file.write('\n#Scan parameters:\n')
            scan_paras = {'SuperScan FOV value': str(self.frame_parameters.get('fov')) + ' nm',
                          'Image size': str(self.frame_parameters.get('size_pixels'))+' px',
                          'Pixel time': str(self.frame_parameters.get('pixeltime'))+' us',
                          'Offset between images': str(self.offset)+' x image size',
                          'Scan rotation': str(self.frame_parameters.get('rotation')) +' deg',
                          'Detectors': str(self.detectors)}
            for key, value in scan_paras.items():
                config_file.write('{0:25}{1:}\n'.format(key+':', value))
        
            config_file.close()
            
def find_offset_and_rotation(as2, superscan):
    """
    This function finds the current rotation of the scan with respect to the stage coordinate system and the offset that has to be set between two neighboured images when no overlap should occur.
    It takes no input arguments, so the current frame parameters are used for image acquisition.

    It returns a tuple of the form (rotation(degrees), offset(fraction of images)).

    """

    frame_parameters = superscan.get_frame_parameters()

    imsize = frame_parameters['fov_nm']

    image_grabber_parameters = {'size_pixels': frame_parameters['size'], 'rotation': 0,
                                'pixeltime': frame_parameters['pixel_time_us'], 'fov': frame_parameters['fov_nm']}

    leftX = vt.as2_get_control(as2, 'StageOutX')
    vt.as2_set_control(as2, 'StageOutX', leftX + 6.0*imsize)
    time.sleep(5)

    image1 = autotune.image_grabber(frame_parameters=image_grabber_parameters, detectors={'MAADF': True, 'HAADF': False})
    #Go to the right by one half image size
    vt.as2_set_control('StageOutX', leftX + 6.5*imsize)
    time.sleep(3)
    image2 = autotune.image_grabber(frame_parameters=image_grabber_parameters, detectors={'MAADF': True, 'HAADF': False})
    #find offset between the two images
    try:
        frame_rotation, frame_distance = autoalign.rot_dist_fft(image1, image2)
    except:
        raise

    return (frame_rotation, frame_distance)

def find_nearest_neighbors(number, target, points):
    """
    Finds the nearest neighbor(s) of a point (target) in a number of points (points).

    Parameters
    -----------
    number : Integer
        Number of nearest neighbors that should be returned.
    target : Tuple
        Point of which the nearest neighbors will be returned. Length of
        the tuple is arbitrary, the first two entries are assumed to be (x,y)
    points : List of tuples
        Points in which to search for the nearest neighbors.
        Again, the first two entries are assumed to be (x,y)

    Returns
    --------
    nearest_neighbors : List of tuples
        The point with the smallest distance to target is at the first position.
        The tuples are the same as in the imput with an additional entry at their first position
        which is the distance to target.
    """
    nearest = []
    for i in range(number):
        nearest.append((np.inf,))

    for point in points:
        distance = np.sqrt((target[0]-point[0])**2 + (target[1]-point[1])**2)
        if distance < nearest[0][0]:
            nearest[0] = (distance,) + point
            nearest.sort(reverse=True)

    return nearest.sort()