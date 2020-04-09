import numpy as np
from scipy.stats import truncnorm, gamma
import datetime
import math
from config import *

def _sample_viral_load_gamma(rng, shape_mean=4.5, shape_std=.15, scale_mean=1., scale_std=.15):
	""" This function samples the shape and scale of a gamma distribution, then returns it"""
	shape = rng.normal(shape_mean, shape_std)
	scale = rng.normal(scale_mean, scale_std)
	return gamma(shape, scale=scale)


def _sample_viral_load_piecewise(rng):
	""" This function samples a piece-wise linear viral load model which increases, plateaus, and drops """
	# https://stackoverflow.com/questions/18441779/how-to-specify-upper-and-lower-limits-when-using-numpy-random-normal
	plateau_start = truncnorm((PLATEAU_START_CLIP_LOW - PLATEAU_START_MEAN)/PLATEAU_START_STD, (PLATEAU_START_CLIP_HIGH - PLATEAU_START_MEAN) / PLATEAU_START_STD, loc=PLATEAU_START_MEAN, scale=PLATEAU_START_STD).rvs(1, random_state=rng)
	plateau_end = plateau_start + truncnorm((PLATEAU_DURATION_CLIP_LOW - PLATEAU_DURATION_MEAN)/PLEATEAU_DURATION_STD,
											(PLATEAU_DURATION_CLIP_HIGH - PLATEAU_DURATION_MEAN) / PLEATEAU_DURATION_STD,
											loc=PLATEAU_DURATION_MEAN, scale=PLEATEAU_DURATION_STD).rvs(1, random_state=rng)
	recovered = plateau_end + truncnorm((plateau_end - RECOVERY_MEAN) / RECOVERY_STD,
										(RECOVERY_CLIP_HIGH - RECOVERY_MEAN) / RECOVERY_STD,
										loc=RECOVERY_MEAN, scale=RECOVERY_STD).rvs(1, random_state=rng)
	plateau_height = rng.uniform(MIN_VIRAL_LOAD, MAX_VIRAL_LOAD)
	return plateau_height, plateau_start, plateau_end, recovered

def _normalize_scores(scores):
    return np.array(scores)/np.sum(scores)

# &canadian-demgraphics
def _get_random_age(rng):
	# random normal centered on 50 with stdev 25
	draw = rng.normal(50, 25, 1)
	if draw < 0:
		# if below 0, shift to a bump centred around 30
		age = round(30 + rng.normal(0, 4))
	else:
		age = round(float(draw))
	return age

def _get_random_area(location_type, num, total_area, rng):
	''' Using Dirichlet distribution since it generates a "distribution of probabilities" 
	which will ensure that the total area allotted to a location type remains conserved 
	while also maintaining a uniform distribution'''
	perc_dist = {"store":0.15, "misc":0.15, "workplace":0.2, "household":0.3, "park":0.5}
	
	# Keeping max at area/2 to ensure no location is allocated more than half of the total area allocated to its location type 
	area = rng.dirichlet(np.ones(math.ceil(num/2)))*(perc_dist[location_type]*total_area/2)
	area = np.append(area,rng.dirichlet(np.ones(math.floor(num/2)))*(perc_dist[location_type]*total_area/2))
	
	return area

def _draw_random_discreet_gaussian(avg, scale, rng):
    # https://stackoverflow.com/a/37411711/3413239
    return int(truncnorm(a=-1, b=1, loc=avg, scale=scale).rvs(1, random_state=rng).round().astype(int)[0])

def _json_serialize(o):
    if isinstance(o, datetime.datetime):
        return o.__str__()

def compute_distance(loc1, loc2):
    return np.sqrt((loc1.lat - loc2.lat) ** 2 + (loc1.lon - loc2.lon) ** 2)
