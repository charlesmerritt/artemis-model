def main():
    # Initialize state (enriched TreeMap)
    artemis = Artemis(treemap_tif="data/raw/TreeMap-2022/Data/TreeMap2022_CONUS.tif", time_horizon=50, engine="FVS", verbose=True)
    
    # Parallelize FVS projections across management units
    # TODO: Implement parallelization
    artemis.parallel(stands="some_stand_polygons")

    # Project each unit for 5 years with FVS
    artemis.project(5)

    # Check management thresholds for each owner type in each area
    # TODO: Implement threshold checking

    # If thresholds are met, generate prescriptions
    # TODO: Implement prescription generation

    # Apply prescriptions to state
    # TODO: Implement prescription application

    # Repeat for next year until end of time horizon


if __name__ == "__main__":
    main()
