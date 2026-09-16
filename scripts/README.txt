Here is the current order of operations for running the LETO Script

the instruction for getting the code to run are all in the scripts themselves, however this will focus on what order to run them in.

First set up a gdb and fill out the proper User Inputs then run:

 	"LETOV1.1"


Then after that run:


	"LETO_CSV_PIPELINE"

	This file take the layer output from your .gdb as well as the FIA tree data and compiles it into two primary files we are working with. 
	"treeinit.csv" and "standinit.csv" This may be the file which your variant of FVS takes in. However these do not yet have info like Plot BAF. FVS variant, and FVS state so tread lightly.
	it may make more sense to take back out of the .db and into .CSVs

Next:


	"Create_FVS_Database"

	This will output a SQLite .db file with the info from "standinit.csv" and "treeinit.csv" formatted into a db which can run in the FVS GUI. You may have to mess with this step and the previous to make the outputs compatible with your version. 
	I would recommend giving chat as many of the scripts and files as you can as an example. It seems to be very good at navigating these format and file related transitions in python
	I have found FVS to be particularly sensitive about this stuff.

Next: 
	You can now run the .db created by the last script in FVS if this all worked. It let you download a output.db with treelists.



Finally:
	"Join_FVS_output_to_arc"

	This uses the MU_ID which is the stand parent ID to join the FVS outputs back to the original stands we created in Arc. It is formatted to the dowloadabe .db after running the GUI.
	You may not need this but it could be valuable.
	Again chat could probably rewrite this script easily for a different form of output

