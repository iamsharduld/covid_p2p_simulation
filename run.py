from monitors import EventMonitor, TimeMonitor, SEIRMonitor
from base import *
from utils import _draw_random_discreet_gaussian, _get_random_age, _get_random_area
import datetime
import click
from config import TICK_MINUTE, LOCATION_DISTRIBUTION, HUMAN_DISTRIBUTION
import numpy as np
import math


@click.group()
def simu():
    pass


@simu.command()
@click.option('--n_people', help='population of the city', type=int, default=100)
@click.option('--n_stores', help='number of grocery stores in the city', type=int, default=100)
@click.option('--n_parks', help='number of parks in the city', type=int, default=20)
@click.option('--n_misc', help='number of non-essential establishments in the city', type=int, default=100)
@click.option('--init_percent_sick', help='% of population initially sick', type=float, default=0.01)
@click.option('--simulation_days', help='number of days to run the simulation for', type=int, default=30)
@click.option('--outfile', help='filename of the output (file format: .pkl)', type=str, required=False)
@click.option('--print_progress', is_flag=True, help='print the evolution of days', default=False)
@click.option('--seed', help='seed for the process', type=int, default=0)
def sim(n_stores=None, n_people=None, n_parks=None, n_misc=None,
        init_percent_sick=0, store_capacity=30, misc_capacity=30,
        start_time=datetime.datetime(2020, 2, 28, 0, 0),
        simulation_days=10,
        outfile=None,
        print_progress=False, seed=0):
    from simulator import Human
    monitors = run_simu(
        n_stores=n_stores, n_people=n_people, n_parks=n_parks, n_misc=n_misc,
        init_percent_sick=init_percent_sick, store_capacity=store_capacity, misc_capacity=misc_capacity,
        start_time=start_time,
        simulation_days=simulation_days,
        outfile=outfile,
        print_progress=print_progress,
        seed=seed
    )
    monitors[0].dump(outfile)
    return monitors[0].data


@simu.command()
@click.option('--toy_human', is_flag=True, help='run the Human from toy.py')
def base(toy_human):
    if toy_human:
        from toy import Human
    else:
        from simulator import Human
    import pandas as pd
    import cufflinks as cf
    cf.go_offline()

    monitors = run_simu(
        n_people=1000,
        init_percent_sick=0.01, store_capacity=30, misc_capacity=30,
        start_time=datetime.datetime(2020, 2, 28, 0, 0),
        simulation_days=30,
        outfile=None,
        print_progress=False, seed=0, Human=Human,
    )
    stats = monitors[1].data
    x = pd.DataFrame.from_dict(stats).set_index('time')
    fig = x[['susceptible', 'exposed', 'infectious', 'removed']].iplot(asFigure=True, title="SEIR")
    fig.show()

    fig = x['R'].iplot(asFigure=True, title="R0")
    fig.show()

@simu.command()
def tune():
    from simulator import Human
    import pandas as pd
    import cufflinks as cf
    import matplotlib.pyplot as plt
    import networkx as nx
    import seaborn as sns
    import io
    import glob

    cf.go_offline()

    monitors, tracker = run_simu(n_people=100, init_percent_sick=0.01,
        store_capacity=30, misc_capacity=30,
        start_time=datetime.datetime(2020, 2, 28, 0, 0),
        simulation_days=60,
        outfile=None,
        print_progress=True, seed=0, Human=Human, other_monitors=[]
    )
    stats = monitors[1].data
    # x = pd.DataFrame.from_dict(stats).set_index('time')
    # fig = x[['susceptible', 'exposed', 'infectious', 'removed']].iplot(asFigure=True, title="SEIR")
    # fig.show()

    # fig = x['R'].iplot(asFigure=True, title="R0")
    # fig.show()

    x = pd.DataFrame.from_dict(stats).set_index('time')
    x = pd.DataFrame.from_dict(tracker.contacts['all'])
    x = x[sorted(x.columns)]
    x = x + x.transpose()
    x /= x.sum(1)

    x = pd.DataFrame.from_dict(tracker.contacts['human_infection'])
    x = x[sorted(x.columns)]
    # fig = x.iplot(kind='heatmap', asFigure=True)
    # fig.show()
    #
    x = tracker.contacts['env_infection']
    import pdb; pdb.set_trace()
    g = tracker.infection_graph
    nx.nx_pydot.write_dot(g,'DiGraph.dot')
    pos = nx.drawing.nx_agraph.graphviz_layout(g, prog='dot')
    nx.draw_networkx(g, pos, with_labels=True)
    plt.show()

    # types = sorted(LOCATION_DISTRIBUTION.keys())
    # ages = sorted(HUMAN_DISTRIBUTION.keys(), key = lambda x:x[0])
    # for hour, v1 in tracker.transition_probability.items():
    #     images = []
    #     fig,ax =  plt.subplots(3,2, figsize=(18,12), sharex=True, sharey=False)
    #     pos = {0:(0,0), 1:(0,1), 2:(1,0), 3:(1,1), 4:(2,0), 5:(2,1)}
    #
    #     for age_bin in range(len(ages)):
    #         v2 = v1[age_bin]
    #         x = pd.DataFrame.from_dict(v2, orient='index')
    #         x = x.reindex(index=types, columns=types)
    #         x = x.div(x.sum(1), axis=0)
    #         g = sns.heatmap(x, ax=ax[pos[age_bin][0]][pos[age_bin][1]],
    #             linewidth=0.5, linecolor='black', annot=True, vmin=0.0, vmax=1.0, cmap=sns.cm.rocket_r)
    #         g.set_title(f"{ages[age_bin][0]} <= age < {ages[age_bin][1]}")
    #
    #     fig.suptitle(f"Hour {hour}", fontsize=16)
    #     fig.savefig(f"images/hour_{hour}.png")

@simu.command()
def test():
    import unittest
    loader = unittest.TestLoader()
    start_dir = 'tests'
    suite = loader.discover(start_dir, pattern='*_test.py')

    runner = unittest.TextTestRunner()
    runner.run(suite)

def run_simu(n_people=None, init_percent_sick=0, store_capacity=30, misc_capacity=30,
             start_time=datetime.datetime(2020, 2, 28, 0, 0),
             simulation_days=10,
             outfile=None,
             print_progress=False, seed=0, Human=None, other_monitors=[]):

    if Human is None:
        from simulator import Human

    # n_stores, n_workplaces, n_schools, n_senior_residencies, n_houses, n_parks, n_miscs = City.setup_locations(n_people)

    rng = np.random.RandomState(seed)
    env = Env(start_time)
    # city_limit = ((0, 1000), (0, 1000))
    city_x_range = (0,1000)
    city_y_range = (0,1000)
    # total_area = (city_limit[0][1]-city_limit[0][0])*(city_limit[1][1]-city_limit[1][0])
    # area_dict = {
    #         'store':_get_random_area('store', n_stores, total_area, rng),
    #         'workplace':_get_random_area('workplace', n_workplaces, total_area, rng),
    #         'school':_get_random_area('school', n_schools, total_area, rng),
    #         'senior_residency':_get_random_area('senior_residency', n_senior_residencies, total_area, rng),
    #         'park':_get_random_area('park', n_parks, total_area, rng),
    #         'misc':_get_random_area('misc', n_miscs, total_area, rng),
    #         'household':_get_random_area('household', n_houses, total_area, rng),
    #         }

    # stores = [
    #     Location(
    #         env, rng,
    #         area = area_dict['store'][i],
    #         name=f'store{i}',
    #         location_type='store',
    #         lat=rng.randint(*city_limit[0]),
    #         lon=rng.randint(*city_limit[1]),
    #         social_contact_factor=0.6,
    #         surface_prob=[0.1, 0.1, 0.3, 0.2, 0.3],
    #         capacity=_draw_random_discreet_gaussian(store_capacity, int(0.5 * store_capacity), rng),
    #     )
    #     for i in range(n_stores)]
    #
    # parks = [
    #     Location(
    #         env, rng,
    #         social_contact_factor=0.05,
    #         name=f'park{i}',
    #         area = area_dict['park'][i],
    #         location_type='park',
    #         lat=rng.randint(*city_limit[0]),
    #         lon=rng.randint(*city_limit[1]),
    #         surface_prob=[0.7, 0.05, 0.05, 0.1, 0.1]
    #     )
    #     for i in range(n_parks)
    # ]
    # households = [
    #     Location(
    #         env, rng,
    #         social_contact_factor=1,
    #         name=f'household{i}',
    #         location_type='household',
    #         area = area_dict['household'][i],
    #         lat=rng.randint(*city_limit[0]),
    #         lon=rng.randint(*city_limit[1]),
    #         surface_prob=[0.05, 0.05, 0.05, 0.05, 0.8]
    #     )
    #     for i in range(int(n_people / 2))
    # ]
    # workplaces = [
    #     Location(
    #         env, rng,
    #         social_contact_factor=0.3,
    #         name=f'workplace{i}',
    #         location_type='workplace',
    #         area = area_dict['workplace'][i],
    #         lat=rng.randint(*city_limit[0]),
    #         lon=rng.randint(*city_limit[1]),
    #         surface_prob=[0.1, 0.1, 0.3, 0.2, 0.3]
    #     )
    #     for i in range(int(n_people / 30))
    # ]
    # miscs = [
    #     Location(
    #         env, rng,
    #         social_contact_factor=1,
    #         capacity=_draw_random_discreet_gaussian(misc_capacity, int(0.5 * misc_capacity), rng),
    #         name=f'misc{i}',
    #         area = area_dict['misc'][i],
    #         location_type='misc',
    #         lat=rng.randint(*city_limit[0]),
    #         lon=rng.randint(*city_limit[1]),
    #         surface_prob=[0.1, 0.1, 0.3, 0.2, 0.3]
    #     ) for i in range(n_misc)
    # ]
    city = City(env, n_people, rng, city_x_range, city_y_range, start_time, init_percent_sick, Human)

    # humans = [
    #     Human(
    #         env=env,
    #         name=i,
    #         rng=rng,
    #         age=_get_random_age_multinomial(rng),
    #         infection_timestamp=start_time if i < n_people * init_percent_sick else None,
    #         household=rng.choice(households),
    #         workplace=rng.choice(workplaces),
    #         rho=0.6,
    #         gamma=0.21
    #     )
    #     for i in range(n_people)]

    # city = City(stores=stores, parks=parks, humans=humans, miscs=miscs)
    monitors = [EventMonitor(f=120), SEIRMonitor(f=1440)]

    # run the simulation
    if print_progress:
        monitors.append(TimeMonitor(1440)) # print every day

    if other_monitors:
        monitors += other_monitors

    for human in city.humans:
        env.process(human.run(city=city))

    for m in monitors:
        env.process(m.run(env, city=city))
    env.run(until=simulation_days * 24 * 60 / TICK_MINUTE)
    return monitors, city.tracker


if __name__ == "__main__":
    simu()
