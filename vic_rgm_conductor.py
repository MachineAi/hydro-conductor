#!/usr/bin/env python

""" This script orchestrates a coupled Variable Infiltration Capacity (VIC) and Regional Glacier Model (RGM) run. """

import sys
import argparse
import subprocess
import numpy as np
import h5py
from collections import defaultdict


vic_full_path = '/home/mfischer/code/vic/vicNl'
#initial_vic_global_file = '/home/mfischer/vic_dev/input/place/glb_base_PLACE_19601995_VIC4.1.2_outNETCDF_initial.txt' 
#vic_global_file = '/home/mfischer/vic_dev/input/place/glb_base_PLACE_19601995_VIC4.1.2_outNETCDF.txt' 
#vpf_full_path = '/home/mfischer/vic_dev/input/place/vpf_place_100m.txt' 
# path to Glacier Mass Balance files
gmb_path = '/home/mfischer/vic_dev/out/testing/gmb_files/'

class MyParser(argparse.ArgumentParser):
    def error(self, message):
        sys.stderr.write('error: %s]n' % message)
        self.print_help()
        sys.exit(2)

def get_global_parameters(global_parm_file):
	""" Parses the VIC global parameters file created by the user that will be needed to conduct the VIC-RGM run """
	# Important parms: STARTYEAR, ENDYEAR, GLACIER_ID, GLACIER_ACCUM_START_YEAR, GLACIER_ACCUM_INTERVAL, STATENAME, VEGPARAM, SOIL, SNOW_BAND, RESULT_DIR
	global_parms = {}
	with open(global_parm_file, 'r') as f:
		for line in iter(f):
			#print 'line: {}'.format(line)
			if not line.isspace() and line[0] is not '#':
				columns = line.split()
				#print 'columns: {}'.format(columns)
				num_columns = len(columns)
				parm_name = columns[0]
				try:
					if global_parms[parm_name]: # if we've already read one or more entries of this parm_name
#						print 'parm {} exists already'.format(parm_name)
						for i in range(1, num_columns):
							temp_list.append(columns[i])
						global_parms[parm_name].append(temp_list)
				except:
					global_parms[parm_name] = []
					if num_columns == 2:
						global_parms[parm_name].append(columns[1])
					elif num_columns > 2:
						temp_list = []
						for i in range(1, num_columns):
							temp_list.append(columns[i])
						global_parms[parm_name].append(temp_list)
		#				print 'global_parms[{}]: {}'.format(parm_name,global_parms[parm_name])
		f.close()
	return global_parms

def get_grid_cell_ids(veg_parm_file):
	""" Parses the vegetation parameter file for the grid cell IDs that will be used in the simulation """
	cell_ids = []
	with open(veg_parm_file, 'r') as f:
		for line in iter(f):
			columns = line.split()
			if len(columns) == 2:
				cell_ids.append(columns[0])
		f.close()
	return cell_ids

def get_rgm_pixel_mapping(pixel_map_file):
	""" Parses the VIC-to-RGM pixel-to-grid cell map file and initialises a 2D map of valid RGM pixels to use as one input to RGM 
	   	pixel_grid is a list of lists of dimensions num_rows_rpg x num_cols_rpg, each element containing:
	 	[<elevation_band>, <median elevation>, <VIC_cell_ID>]"""
	pixel_grid = []
	with open(pixel_map_file, 'r') as f:
		num_cols_rpg = 0
		num_rows_rpg = 0
		for line in iter(f):
			#print 'line: {}'.format(line)
			split_line = line.split()
			if split_line[0] == 'NCOLS':
				num_cols_rpg = int(split_line[1])
				#print 'num_cols_rpg: {}'.format(num_cols_rpg)
				if num_cols_rpg and num_rows_rpg:
					pixel_grid = [[0 for x in range(0, num_cols_rpg)] for x in range(0, num_rows_rpg)]
			elif split_line[0] == 'NROWS':
				num_rows_rpg = int(split_line[1])
				#print 'num_rows_rpg: {}'.format(num_rows_rpg)
				if num_cols_rpg and num_rows_rpg:
					pixel_grid = [[0 for x in range(0, num_cols_rpg)] for x in range(0, num_rows_rpg)]
			elif split_line[0][0] == '"': # column header row / comments
				#print 'comment line: {}'.format(split_line)
				pass
			else:
				row_num = int(split_line[1])
				col_num = int(split_line[2])
				#print 'populating row: {}, col: {}'.format(row_num, col_num)
				# put band, elevation, and VIC cell ID triplet in the row,col position of pixel_grid
				pixel_grid[row_num-1][col_num-1] = [split_line[3], split_line[4], split_line[5]]
			
			#print 'columns: {}'.format(columns)
	return pixel_grid, num_rows_rpg, num_cols_rpg

def get_mass_balance_polynomials(state):
	""" Extracts the Glacier Mass Balance polynomial for each grid cell from an open VIC state file """
	gmb_info = state['GLAC_MASS_BALANCE_INFO']
	cell_count = len(gmb_info[0])
	if cell_count != len(cell_ids):
		print 'get_mass_balance_polynomials: The number of VIC cells read from the state file {} and the vegetation parameter file ({}) disagree. Exiting.\n'.format(cell_count, len(cell_ids))
		sys.exit(0)
	gmb_polys = {}
	for i in range(0, cell_count):
		cell_id = str(int(gmb_info[0][i][0]))
		if cell_id not in cell_ids:
			print 'get_mass_balance_polynomials: Cell ID {} was not found in the list of VIC cell IDs read from the vegetation parameters file. Exiting.\n'.format(cell_id)
			#sys.exit(0)
		gmb_polys[cell_id] = [gmb_info[0][i][1], gmb_info[0][i][2], gmb_info[0][i][3]]
	return gmb_polys

def mass_balances_to_rgm_grid(gmb_polys, rgm_pixel_grid):
	""" Translate mass balances from grid cell GMB polynomials to 2D RGM pixel grid to use as one of the inputs to RGM """
	mass_balance_grid = [[0 for x in range(0, num_cols_rpg)] for x in range(0, num_rows_rpg)]
	for row in range(0, num_rows_rpg):
		for col in range(0, num_cols_rpg):
			pixel = rgm_pixel_grid[row][col]
			band = pixel[0]
			median_elev = pixel[1]
			cell_id = pixel[2]
			# only grabbing pixels that fall within a VIC cell
			if cell_id != 'NA':
				# check that the cell_id agrees with what was read from the veg_parm_file
				if cell_id not in cell_ids:
					print 'mass_balances_to_rgm_grid: Cell ID {} was not found in the list of VIC cell IDs read from the vegetation parameters file. Exiting.\n'.format(cell_id)
					#sys.exit(0)
				mass_balance_grid[row][col] = gmb_polys[cell_id][0] + median_elev * (gmb_polys[cell_id][1] + median_elev * gmb_polys[cell_id][2])
	return mass_balance_grid			
		

def write_mbg_to_file(mass_balance_grid):
	""" Writes the 2D Mass Balance Grid to ASCII file in the input format expected by the RGM, and returns a filename """
		pass

if __name__ == '__main__':
	
	parser = MyParser()
	parser.add_argument('--g', action="store", dest="vic_global_file", type=str, help = 'file name and path of the VIC global parameters file')
#	parser.add_argument('--vpf', action="store", dest="veg_parm_file", type=str, help = 'file name and path of the initial Vegetation Parameter File (VPF)')
	parser.add_argument('--sdem', action="store", dest="surf_dem_file", type=str, help = 'file name and path of the initial Surface Digital Elevation Model (SDEM) file')
	parser.add_argument('--bdem', action="store", dest="bed_dem_file", type=str, help = 'file name and path of the Bed Digital Elevation Model (BDEM) file')
	parser.add_argument('--pixel-map', action="store", dest="pixel_map_file", type=str, help = 'file name and path of the VIC Grid to RGM Pixel Mapping file')

	if len(sys.argv) == 1:
    	parser.print_help()
    	sys.exit(1)
	options = parser.parse_args()
	vic_global_file = options.vic_global_file
	initial_veg_parm_file = options.veg_parm_file
	initial_surf_dem_file = options.surf_dem_file
	bed_dem_file = options.bed_dem_file
	pixel_map_file = options.pixel_map_file

	# Get all VIC global parameters
	global_parms = get_global_parameters(vic_global_file)

	# Get all grid cell ID numbers for this simulation from the initial Vegetation Parameter File (iVPF)
	cell_ids = get_grid_cell_ids(global_parms['VEGPARAM'])

	# Open and read VIC-grid-to-RGM-pixel mapping file
	# rgm_pixel_grid is a list of lists of dimensions num_rows_rpg x num_cols_rpg, each element containing:
	# [<elevation_band>, <median elevation>, <VIC_grid_cell_num>]
	rgm_pixel_grid, num_rows_rpg, num_cols_rpg = get_rgm_pixel_mapping(pixel_map_file)

	# Open and read Bed Digital Elevation Map (BDEM) file into 2D bdem_grid
	bdem_grid = get_bdem(bed_dem_file)

	start_year = global_parms['STARTYEAR']
	end_year = global_parms['ENDYEAR']
	global_file = vic_global_file
	veg_file = initial_veg_parm_file
	sdem_file = initial_surf_dem_file

	for year in range(start_year, end_year):
		# Call initial VIC run starting at year 0. 
		if year > start_year:
			global_file = temp_gpf
			veg_file = temp_vpf
			sdem_file = temp_sdem

		# Run VIC for a year.  This will do the following:
		# 1) create, if needed, and write a polynomial line to the Glacier Mass Balance (GMB) file
		# 2) save VIC state at the end of the year
		subprocess.check_call([vic_full_path, "-g", global_file], shell=False, stderr=subprocess.STDOUT)

		# 3. Open VIC NetCDF state file and get the most recent GMB polynomial for each grid cell being modeled
		state_filename = str(global_parms['STATENAME'][0]) + "_" + str(year) + str(global_parms['STATEMONTH'][0]) + str(global_parms['STATEDAY'][0])
		state = h5py.File(state_filename, 'r+')
		gmb_polys = get_mass_balance_polynomials(state)
			
		# 4. Translate mass balances using grid cell GMB polynomials and current veg_parm_file into a 2D RGM mass balance grid (MBG)
		mass_balance_grid = mass_balances_to_rgm_grid(gmb_polys, rgm_pixel_grid)
		# write MBG to ASCII file to direct the RGM to use as input
		mass_balance_file = write_mbg_to_file(mass_balance_grid)

		# 5. Run RGM for one year, passing MBG, BDEM, SDEM
		subprocess.check_call([rgm_full_path, "-p", rgm_params_file, "-b", bed_dem_file, "-d", sdem_file, "-m" mass_balance_file, "-s", 0, "-e", 0 ], shell=False, stderr=subprocess.STDOUT)
	
		# 6. Read in new Surface DEM file from RGM output
		sdem_file = get_temp_sdem_filename()
		temp_sdem = read_sdem_file()

		# 7. Update glacier mask
		glacier_mask_grid = update_glacier_mask(temp_sdem, bdem_grid)
		
		# 8.1 Update HRUs in VIC state file 

		# 8.2 Write / Update temp_vpf

		# 9. Write / Update temp_gpf
		# need to introduce INIT_STATE line with most current state_filename (does not exist in the first read-in of global_parms)