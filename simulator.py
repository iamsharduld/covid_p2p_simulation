# -*- coding: utf-8 -*-
import simpy

import itertools
import numpy as np
from collections import defaultdict, namedtuple
import datetime
from bitarray import bitarray
import operator
import math

from utils import _normalize_scores, _get_random_age, _get_random_sex, _get_all_symptoms, \
    _get_preexisting_conditions, _draw_random_discreet_gaussian, _json_serialize, _sample_viral_load_piecewise, \
    _get_random_area, _encode_message, _decode_message, float_to_binary, binary_to_float, _reported_symptoms, _get_mask_wearing
from config import *  # PARAMETERS

from base import *

if COLLECT_LOGS is False:
    Event = DummyEvent


class Visits(object):

    def __init__(self):
        self.parks = defaultdict(int)
        self.stores = defaultdict(int)
        self.hospitals = defaultdict(int)
        self.miscs = defaultdict(int)

    @property
    def n_parks(self):
        return len(self.parks)

    @property
    def n_stores(self):
        return len(self.stores)

    @property
    def n_hospitals(self):
        return len(self.hospitals)

    @property
    def n_miscs(self):
        return len(self.miscs)


class Human(object):

    def __init__(self, env, name, age, rng, infection_timestamp, household, workplace, profession, rho=0.3, gamma=0.21, symptoms=[],
                 test_results=None, sim_days=0):
        self.env = env
        self._events = []
        self.name = f"human:{name}"
        self.rng = rng
        self.profession = profession
        self.death = False

        self.age = age
        self.sex = _get_random_sex(self.rng)
        self.preexisting_conditions = _get_preexisting_conditions(self.age, self.sex, self.rng)

        self.assign_household(household)
        self.workplace = workplace
        self.rho = rho
        self.gamma = gamma
        self.rest_at_home = False # to track mobility due to symptoms

        self.visits = Visits()
        self.travelled_recently = self.rng.rand() > 0.9

        # &carefulness
        if self.rng.rand() < P_CAREFUL_PERSON:
            self.carefulness = (round(self.rng.normal(55, 10)) + self.age/2) / 100
        else:
            self.carefulness = (round(self.rng.normal(25, 10)) + self.age/2) / 100
        self.mask_wearing = _get_mask_wearing(self.carefulness, sim_days, self.rng)

        age_modifier = 1
        if self.age > 40 or self.age < 12:
            age_modifier = 2
        self.has_cold = self.rng.rand() < P_COLD * age_modifier
        self.has_flu = self.rng.rand() < P_FLU * age_modifier
        self.has_app = self.rng.rand() < (P_HAS_APP / age_modifier) + (self.carefulness / 2)
        self.incubation_days = _draw_random_discreet_gaussian(AVG_INCUBATION_DAYS, SCALE_INCUBATION_DAYS, self.rng)

        # Indicates whether this person will show severe signs of illness.
        self.infection_timestamp = infection_timestamp
        self.is_immune = False
        self.recovered_timestamp = datetime.datetime.min
        self.gets_really_sick = self.rng.random() >= 0.8 + (age/100)
        self.gets_extremely_sick = self.gets_really_sick and self.rng.random() >= 0.7 # &severe; 30% of severe cases need ICU
        self.never_recovers = self.rng.random() <= P_NEVER_RECOVERS[min(math.floor(self.age/10),8)]
        self.obs_hospitalized = False
        self.obs_in_icu = False

        # &symptoms, &viral-load
        # probability of being asymptomatic is basically 50%, but a bit less if you're older
        # and a bit more if you're younger
        self.is_asymptomatic = self.rng.rand() > (BASELINE_P_ASYMPTOMATIC - (self.age - 50) * 0.5) / 100
        self.asymptomatic_infection_ratio = ASYMPTOMATIC_INFECTION_RATIO if self.is_asymptomatic else 0.0 # draw a beta with the distribution in documents

        self.recovery_days = _draw_random_discreet_gaussian(AVG_RECOVERY_DAYS, SCALE_RECOVERY_DAYS, self.rng) # make it IQR &recovery
        self.viral_load_plateau_height, self.viral_load_plateau_start, self.viral_load_plateau_end, self.viral_load_recovered = _sample_viral_load_piecewise(rng, age=age)
        self.all_symptoms = _get_all_symptoms(
                          np.ndarray.item(self.viral_load_plateau_start), np.ndarray.item(self.viral_load_plateau_end),
                          np.ndarray.item(self.viral_load_recovered), age=self.age, incubation_days=self.incubation_days,
                                                          really_sick=self.gets_really_sick, extremely_sick=self.gets_extremely_sick,
                          rng=self.rng, preexisting_conditions=self.preexisting_conditions)
        self.all_reported_symptoms = _reported_symptoms(self.all_symptoms, self.rng, self.carefulness)

        # counters and memory
        self.r0 = []
        self.has_logged_symptoms = self.has_app and any(self.symptoms) and rng.rand() < 0.5
        self.has_logged_test = self.has_app and self.test_results and rng.rand() < 0.5
        self.has_logged_info = self.has_app and rng.rand() < 0.5
        self.last_state = self.state
        self.n_infectious_contacts = 0
        self.symptom_start_time = None

        self.obs_age = self.age if self.has_app and self.has_logged_info else None
        self.obs_sex = self.sex if self.has_app and self.has_logged_info else None
        self.obs_preexisting_conditions = self.preexisting_conditions if self.has_app and self.has_logged_info else None
        self.obs_symptoms = self.symptoms if self.has_logged_symptoms else None

        # habits
        self.avg_shopping_time = _draw_random_discreet_gaussian(AVG_SHOP_TIME_MINUTES, SCALE_SHOP_TIME_MINUTES, self.rng)
        self.scale_shopping_time = _draw_random_discreet_gaussian(AVG_SCALE_SHOP_TIME_MINUTES,
                                                                  SCALE_SCALE_SHOP_TIME_MINUTES, self.rng)

        self.avg_exercise_time = _draw_random_discreet_gaussian(AVG_EXERCISE_MINUTES, SCALE_EXERCISE_MINUTES, self.rng)
        self.scale_exercise_time = _draw_random_discreet_gaussian(AVG_SCALE_EXERCISE_MINUTES,
                                                                  SCALE_SCALE_EXERCISE_MINUTES, self.rng)

        self.avg_working_minutes = _draw_random_discreet_gaussian(AVG_WORKING_MINUTES, SCALE_WORKING_MINUTES, self.rng)
        self.scale_working_minutes = _draw_random_discreet_gaussian(AVG_SCALE_WORKING_MINUTES, SCALE_SCALE_WORKING_MINUTES, self.rng)

        self.avg_hospital_hours = _draw_random_discreet_gaussian(AVG_HOSPITAL_HOURS, SCALE_HOSPITAL_HOURS, self.rng)
        self.scale_hospital_hours = _draw_random_discreet_gaussian(AVG_SCALE_HOSPITAL_HOURS, SCALE_SCALE_HOSPITAL_HOURS, self.rng)

        self.avg_misc_time = _draw_random_discreet_gaussian(AVG_MISC_MINUTES, SCALE_MISC_MINUTES, self.rng)
        self.scale_misc_time = _draw_random_discreet_gaussian(AVG_SCALE_MISC_MINUTES, SCALE_SCALE_MISC_MINUTES, self.rng)

        #getting the number of shopping days and hours from a distribution
        self.number_of_shopping_days = _draw_random_discreet_gaussian(AVG_NUM_SHOPPING_DAYS, SCALE_NUM_SHOPPING_DAYS, self.rng)
        self.number_of_shopping_hours = _draw_random_discreet_gaussian(AVG_NUM_SHOPPING_HOURS, SCALE_NUM_SHOPPING_HOURS, self.rng)

        #getting the number of exercise days and hours from a distribution
        self.number_of_exercise_days = _draw_random_discreet_gaussian(AVG_NUM_EXERCISE_DAYS, SCALE_NUM_EXERCISE_DAYS, self.rng)
        self.number_of_exercise_hours = _draw_random_discreet_gaussian(AVG_NUM_EXERCISE_HOURS, SCALE_NUM_EXERCISE_HOURS, self.rng)

        #Multiple shopping days and hours
        self.shopping_days = self.rng.choice(range(7), self.number_of_shopping_days)
        self.shopping_hours = self.rng.choice(range(7, 20), self.number_of_shopping_hours)

        #Multiple exercise days and hours
        self.exercise_days = self.rng.choice(range(7), self.number_of_exercise_days)
        self.exercise_hours = self.rng.choice(range(7, 20), self.number_of_exercise_hours)

        #Limiting the number of hours spent shopping per week
        self.max_shop_per_week = _draw_random_discreet_gaussian(AVG_MAX_NUM_SHOP_PER_WEEK, SCALE_MAX_NUM_SHOP_PER_WEEK, self.rng)
        self.count_shop=0

        #Limiting the number of hours spent exercising per week
        self.max_exercise_per_week = _draw_random_discreet_gaussian(AVG_MAX_NUM_EXERCISE_PER_WEEK, SCALE_MAX_NUM_EXERCISE_PER_WEEK, self.rng)
        self.count_exercise=0

        self.work_start_hour = self.rng.choice(range(7, 12), 3)

    def assign_household(self, location):
        self.household = location
        self.location = location
        if self.profession == "retired":
            self.workplace = location

    def __repr__(self):
        return f"H:{self.name}, SEIR:{int(self.is_susceptible)}{int(self.is_exposed)}{int(self.is_infectious)}{int(self.is_removed)}"

    ########### MEMORY OPTIMIZATION ###########
    @property
    def events(self):
        return self._events

    def pull_events(self):
        if self._events:
            events = self._events
            self._events = []
        else:
            events = self._events
        return events

    ########### EPI ###########

    @property
    def is_susceptible(self):
        return not self.is_exposed and not self.is_infectious and not self.is_removed and not self.is_immune

    @property
    def is_exposed(self):
        return self.infection_timestamp is not None and self.env.timestamp - self.infection_timestamp < datetime.timedelta(days=self.incubation_days - INFECTIOUSNESS_ONSET_DAYS)

    @property
    def is_infectious(self):
        return self.infection_timestamp is not None and self.env.timestamp - self.infection_timestamp >= datetime.timedelta(days=self.incubation_days - INFECTIOUSNESS_ONSET_DAYS)

    @property
    def is_removed(self):
        return self.recovered_timestamp == datetime.datetime.max

    @property
    def is_incubated(self):
        return (not self.is_asymptomatic and self.infection_timestamp is not None and
                self.env.timestamp - self.infection_timestamp >= datetime.timedelta(days=self.incubation_days))

    @property
    def state(self):
        return [int(self.is_susceptible), int(self.is_exposed), int(self.is_infectious), int(self.is_removed)]

    @property
    def really_sick(self):
        return self.gets_really_sick and 'severe' in self.symptoms

    @property
    def extremely_sick(self):
        return self.gets_extremely_sick and 'severe' in self.symptoms

    def test_results(self):
        if any(self.symptoms) and self.rng.rand() > P_TEST:
            test_type = 'lab'
            e = ''
            if self.rng.rand() > P_FALSE_NEGATIVE:
                test_result =  'negative'
            else:
                test_result =  'positive'
            return (test_result, test_type)

        return (None, None)

    @property
    def symptoms(self):
        if self.infection_timestamp is not None and not self.is_asymptomatic:
            sickness_day = (self.env.timestamp - self.infection_timestamp).days
            if sickness_day >= len(self.all_symptoms):
                return []
            return self.all_symptoms[sickness_day]
        return []

    def reported_symptoms(self):
        try:
            sickness_day = (self.env.timestamp - self.infection_timestamp).days
            return self.all_reported_symptoms[sickness_day]
        except Exception as e:
            return []

    def reported_symptoms_for_sickness(self):
        try:
            sickness_day = (self.env.timestamp - self.infection_timestamp).days
            all_reported_symptoms_till_day = []
            for day in range(sickness_day+1):
                all_reported_symptoms_till_day.extend(self.all_reported_symptoms[sickness_day])
            return all_reported_symptoms_till_day
        except Exception as e:
            return []

    @property
    def viral_load(self):
        """ Calculates the elapsed time since infection, returning this person's current viral load"""
        if not self.infection_timestamp:
            return 0.
        # calculates the time since infection in days
        time_exposed = (self.env.timestamp - self.infection_timestamp)
        time_exposed_days = time_exposed.days + time_exposed.seconds / 86400 #(seconds in a day)

        # implements the piecewise linear function
        if time_exposed_days < self.viral_load_plateau_start:
            cur_viral_load = self.viral_load_plateau_height * time_exposed_days / self.viral_load_plateau_start
        elif time_exposed_days < self.viral_load_plateau_end:
            cur_viral_load = self.viral_load_plateau_height
        else:
            cur_viral_load = self.viral_load_plateau_height - self.viral_load_plateau_height * (time_exposed_days -self.viral_load_plateau_end) / (self.viral_load_recovered - self.viral_load_plateau_end)

        # the viral load cannot be negative
        if cur_viral_load < 0:
            cur_viral_load = 0.
        return cur_viral_load

    @property
    def infectiousness(self):
        severity_multiplier = 1
        if self.is_infectious:
            if self.gets_really_sick:
              severity_multiplier = 1.25
            if self.extremely_sick:
              severity_multiplier = 1.5
            if 'immuno-compromised' in self.preexisting_conditions:
              severity_multiplier += 0.2
            if 'cough' in self.symptoms:
              severity_multiplier += 0.25
        return self.viral_load * severity_multiplier

    @property
    def wearing_mask(self):
        #TODO there HAS TO BE A BETTER WAY
        today = (self.env.timestamp-self.env.initial_timestamp).days
        if self.location == self.household:
            mask = False
        if self.location.location_type == 'store':
            if self.carefulness > 60:
                mask = True
            else:
                mask = self.mask_wearing[today]
        else:
            mask = self.mask_wearing[today] #baseline p * carefullness

    @property
    def mask_effect(self):
      if self.wearing_mask:
          if  self.workplace.location_type == 'hospital':
              efficacy = MASK_EFFICACY_HEALTHWORKER
          else:
              efficacy = MASK_EFFICACY_NORMIE
          return efficacy
      else:
        return 1

    def how_am_I_feeling(self):
        current_symptoms = self.symptoms
        if current_symptoms == []:
            return 1.0

        if sum(x in current_symptoms for x in ["severe", "extremely_severe", "trouble_breathing"]) > 0:
            return 0.0

        elif sum(x in current_symptoms for x in ["moderate", "mild", "fever"]) > 0:
            return 0.3

        elif sum(x in current_symptoms for x in ["cough", "fatigue", "gastro", "aches"]) > 0:
            return 0.5

        elif sum(x in current_symptoms for x in ["runny_nose", "loss_of_taste"]) > 0:
            return 0.7

        return 0.9

    def assert_state_changes(self):
        next_state = {0:[1], 1:[2], 2:[0, 3]}
        assert sum(self.state) == 1, f"invalid compartment for human:{self.name}"
        if self.last_state != self.state:
            assert self.state.index(1) in next_state[self.last_state.index(1)], f"invalid compartment transition for human:{self.name}"
            self.last_state = self.state


    def run(self, city):
        """
           1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20 21 22 23 24
           State  h h h h h h h h h sh sh h  h  h  ac h  h  h  h  h  h  h  h  h
        """
        self.household.humans.add(self)
        while True:

            # show symptoms
            if self.is_incubated and not self.has_logged_symptoms:
                self.symptom_start_time = self.env.timestamp
                city.tracker.track_generation_times(self.name) # it doesn't count environmental infection or primary case or asymptomatic/presymptomatic infections; refer the definition
                Event.log_symptom_start(self, True, self.env.timestamp)
                self.has_logged_symptoms = True

            # log test
            if (self.is_incubated and
                self.env.timestamp - self.symptom_start_time > datetime.timedelta(days=TEST_DAYS) and
                not self.has_logged_test):
                test_result, test_type = self.test_results()
                Event.log_test(self, test_result, test_type, self.env.timestamp)
                self.has_logged_test = True
                assert self.has_logged_symptoms is True # FIXME: assumption might not hold

            # recover
            if self.is_infectious and self.env.timestamp - self.infection_timestamp >= datetime.timedelta(days=self.recovery_days):
                recovery_duration = self.recovery_days - self.incubation_days + INFECTIOUSNESS_ONSET_DAYS
                city.tracker.track_recovery(self.n_infectious_contacts, recovery_duration)
                if self.never_recovers:
                    self.recovered_timestamp = datetime.datetime.max
                    dead = True
                else:
                    if not REINFECTION_POSSIBLE:
                        self.recovered_timestamp = datetime.datetime.max
                        self.is_immune = not REINFECTION_POSSIBLE
                    else:
                        self.recovered_timestamp = self.env.timestamp
                    self.never_recovers = self.rng.random() <= P_NEVER_RECOVERS[min(math.floor(self.age/10),8)]
                    dead = False

                self.infection_timestamp = None # indicates they are no longer infected
                Event.log_recovery(self, self.env.timestamp, dead)
                if dead:
                    yield self.env.timeout(np.inf)

            self.assert_state_changes()

            # Mobility
            hour, day = self.env.hour_of_day(), self.env.day_of_week()
            if day==0:
                self.count_exercise=0
                self.count_shop=0

            # self.how_am_I_feeling = 1.0 (great) --> rest_at_home = False
            # self.how_am_I_feeling = 0.0 (worst) --> rest_at_home = True
            if not self.rest_at_home:
                # set it once for the rest of the disease path
                if not self.rng.random() < self.how_am_I_feeling():
                    self.rest_at_home = True

            # happens when recovered
            elif self.rest_at_home and self.how_am_I_feeling() == 1.0:
                self.rest_at_home = False

            if self.extremely_sick:
                yield self.env.process(self.excursion(city, "hospital-icu"))

            elif self.really_sick:
                yield self.env.process(self.excursion(city, "hospital"))

            if (not WORK_FROM_HOME and
                not self.env.is_weekend() and
                hour in self.work_start_hour and
                not self.rest_at_home):
                yield self.env.process(self.excursion(city, "work"))

            elif ( hour in self.shopping_hours and
                   day in self.shopping_days and
                   self.count_shop<=self.max_shop_per_week and
                   not self.rest_at_home):
                self.count_shop+=1
                yield self.env.process(self.excursion(city, "shopping"))

            elif ( hour in self.exercise_hours and
                    day in self.exercise_days and
                    self.count_exercise<=self.max_exercise_per_week and
                    not self.rest_at_home):
                self.count_exercise+=1
                yield  self.env.process(self.excursion(city, "exercise"))

            elif (self.env.is_weekend() and
                    self.rng.random() < 0.05 and
                    not self.rest_at_home):
                yield  self.env.process(self.excursion(city, "leisure"))

            # start from house all the time
            yield self.env.process(self.at(self.household, city, 60))

    ############################## MOBILITY ##################################
    @property
    def lat(self):
        return self.location.lat if self.location else self.household.lat

    @property
    def lon(self):
        return self.location.lon if self.location else self.household.lon


    @property
    def obs_lat(self):
        if LOCATION_TECH == 'bluetooth':
            return round(self.lat + self.rng.normal(0, 2))
        else:
            return round(self.lat + self.rng.normal(0, 10))

    @property
    def obs_lon(self):
        if LOCATION_TECH == 'bluetooth':
            return round(self.lon + self.rng.normal(0, 2))
        else:
            return round(self.lon + self.rng.normal(0, 10))

    def excursion(self, city, type):

        if type == "shopping":
            grocery_store = self._select_location(location_type="stores", city=city)
            t = _draw_random_discreet_gaussian(self.avg_shopping_time, self.scale_shopping_time, self.rng)
            with grocery_store.request() as request:
                yield request
                yield self.env.process(self.at(grocery_store, city, t))

        elif type == "exercise":
            park = self._select_location(location_type="park", city=city)
            t = _draw_random_discreet_gaussian(self.avg_exercise_time, self.scale_exercise_time, self.rng)
            yield self.env.process(self.at(park, city, t))

        elif type == "work":
            t = _draw_random_discreet_gaussian(self.avg_working_minutes, self.scale_working_minutes, self.rng)
            yield self.env.process(self.at(self.workplace, city, t))

        elif type == "hospital":
            hospital = self._select_location(location_type=type, city=city)
            if hospital is None: # no more hospitals
                yield self.env.timeout(np.inf)

            t = self.recovery_days -(self.env.timestamp - self.infection_timestamp).total_seconds() / 86400 # DAYS
            yield self.env.process(self.at(hospital, city, t * 24 * 60))

        elif type == "hospital-icu":
            icu = self._select_location(location_type=type, city=city)
            if icu is None:
                yield self.env.timeout(np.inf)

            if len(self.preexisting_conditions) < 2:
                extra_time = self.rng.choice([1, 2, 3], p=[0.5, 0.3, 0.2])
            else:
                extra_time = self.rng.choice([1, 2, 3], p=[0.2, 0.3, 0.5]) # DAYS
            t = self.viral_load_plateau_end[0] - self.viral_load_plateau_start[0] + extra_time

            yield self.env.process(self.at(icu, city, t * 24 * 60))

        elif type == "leisure":
            S = 0
            p_exp = 1.0
            while True:
                if self.rng.random() > p_exp:  # return home
                    yield self.env.process(self.at(self.household, city, 60))
                    break

                loc = self._select_location(location_type='miscs', city=city)
                S += 1
                p_exp = self.rho * S ** (-self.gamma * self.adjust_gamma)
                with loc.request() as request:
                    yield request
                    t = _draw_random_discreet_gaussian(self.avg_misc_time, self.scale_misc_time, self.rng)
                    yield self.env.process(self.at(loc, city, t))
        else:
            raise ValueError(f'Unknown excursion type:{type}')


    def at(self, location, city, duration):
        city.tracker.track_trip(from_location=self.location.location_type, to_location=location.location_type, age=self.age, hour=self.env.hour_of_day())

        # add the human to the location
        self.location = location
        location.add_human(self)
        self.leaving_time = duration + self.env.now
        self.start_time = self.env.now
        area = self.location.area

        # Report all the encounters (epi transmission)
        for h in location.humans:
            if h == self:
                continue

            distance =  np.sqrt(int(area/len(self.location.humans))) + self.rng.randint(MIN_DIST_ENCOUNTER, MAX_DIST_ENCOUNTER)
            t_overlap = min(self.leaving_time, getattr(h, "leaving_time", 60)) - max(self.start_time, getattr(h, "start_time", 60))
            t_near = self.rng.random() * t_overlap
            # if self.rng.random() < 0.001:
                # import pdb; pdb.set_trace()
                # print("distance", distance, "time", t_near)
            contact_condition = distance <= INFECTION_RADIUS and t_near > INFECTION_DURATION
            if contact_condition:
                proximity_factor = (1 - distance/INFECTION_RADIUS) + min((t_near - INFECTION_DURATION)/INFECTION_DURATION, 1)

                infectee = None
                if self.is_infectious:
                    ratio = self.asymptomatic_infection_ratio  if self.is_asymptomatic else 1.0
                    p_infection = self.infectiousness * ratio
                    x_human = self.rng.random() < p_infection * CONTAGION_KNOB

                    if x_human and h.is_susceptible:
                        h.infection_timestamp = self.env.timestamp
                        self.n_infectious_contacts+=1
                        Event.log_exposed(h, self, self.env.timestamp)
                        city.tracker.track_infection('human', from_human=self, to_human=h, location=location, timestamp=self.env.timestamp)
                        # this was necessary because the side-simulation needs to know about the infection time
                        h.historical_infection_timestamp = self.env.timestamp
                        # print(f"{self.name} infected {h.name} at {location}")
                        infectee = h.name

                elif h.is_infectious:
                    ratio = h.asymptomatic_infection_ratio  if h.is_asymptomatic else 1.0
                    p_infection = h.infectiousness * ratio # &prob_infectious
                    x_human = self.rng.random() < p_infection * CONTAGION_KNOB

                    if x_human and self.is_susceptible:
                        self.infection_timestamp = self.env.timestamp
                        h.n_infectious_contacts+=1
                        Event.log_exposed(self, h, self.env.timestamp)
                        city.tracker.track_infection('human', from_human=h, to_human=self, location=location, timestamp=self.env.timestamp)
                        # this was necessary because the side-simulation needs to know about the infection time
                        self.historical_infection_timestamp = self.env.timestamp
                        # print(f"{h.name} infected {self.name} at {location}")
                        infectee = self.name

                city.tracker.track_encounter_events(human1=self, human2=h, location=location, distance=distance, duration=t_near)
                Event.log_encounter(self, h,
                                    location=location,
                                    duration=t_near,
                                    distance=distance,
                                    infectee=infectee,
                                    time=self.env.timestamp
                                    )

        yield self.env.timeout(duration / TICK_MINUTE)

        # environmental transmission
        x_environment = location.contamination_probability > 0 and self.rng.random() < ENVIRONMENTAL_INFECTION_KNOB * location.contamination_probability # &prob_infection
        if x_environment and self.is_susceptible:
            self.infection_timestamp = self.env.timestamp
            Event.log_exposed(self, location,  self.env.timestamp)
            city.tracker.track_infection('env', from_human=None, to_human=self, location=location, timestamp=self.env.timestamp)
            self.historical_infection_timestamp = self.env.timestamp
            # print(f"{self.name} is enfected at {location}")

        location.remove_human(self)


    def _select_location(self, location_type, city):
        """
        Preferential exploration treatment to visit places
        rho, gamma are treated in the paper for normal trips
        Here gamma is multiplied by a factor to supress exploration for parks, stores.
        """
        if location_type == "park":
            S = self.visits.n_parks
            self.adjust_gamma = 1.0
            pool_pref = self.parks_preferences
            locs = city.parks
            visited_locs = self.visits.parks

        elif location_type == "stores":
            S = self.visits.n_stores
            self.adjust_gamma = 1.0
            pool_pref = self.stores_preferences
            locs = city.stores
            visited_locs = self.visits.stores

        elif location_type == "hospital":
            hospital = None
            for hospital in sorted(city.hospitals, key=lambda x:compute_distance(self.location, x)):
                if len(hospital.humans) < hospital.capacity:
                    return hospital
            return None

        elif location_type == "hospital-icu":
            icu = None
            for hospital in sorted(city.hospitals, key=lambda x:compute_distance(self.location, x)):
                if len(hospital.icu.humans) < hospital.icu.capacity:
                    return hospital.icu
            return None

        elif location_type == "miscs":
            S = self.visits.n_miscs
            self.adjust_gamma = 1.0
            pool_pref = [(compute_distance(self.location, m) + 1e-1) ** -1 for m in city.miscs if
                         m != self.location]
            pool_locs = [m for m in city.miscs if m != self.location]
            locs = city.miscs
            visited_locs = self.visits.miscs

        else:
            raise ValueError(f'Unknown location_type:{location_type}')

        if S == 0:
            p_exp = 1.0
        else:
            p_exp = self.rho * S ** (-self.gamma * self.adjust_gamma)

        if self.rng.random() < p_exp and S != len(locs):
            # explore
            cands = [i for i in locs if i not in visited_locs]
            cands = [(loc, pool_pref[i]) for i, loc in enumerate(cands)]
        else:
            # exploit
            cands = [(i, count) for i, count in visited_locs.items()]

        cands, scores = zip(*cands)
        loc = self.rng.choice(cands, p=_normalize_scores(scores))
        visited_locs[loc] += 1
        return loc

    def serialize(self):
        """This function serializes the human object for pickle."""
        # TODO: I deleted many unserializable attributes, but many of them can (and should) be converted to serializable form.
        del self.env
        del self._events
        del self.rng
        del self.visits
        del self.leaving_time
        del self.start_time
        del self.household
        del self.location
        del self.workplace
        del self.viral_load_plateau_start
        del self.viral_load_plateau_end
        del self.viral_load_recovered
        del self.exercise_hours
        del self.exercise_days
        del self.shopping_days
        del self.shopping_hours
        del self.work_start_hour
        del self.infection_timestamp
        del self.recovered_timestamp
        return self
