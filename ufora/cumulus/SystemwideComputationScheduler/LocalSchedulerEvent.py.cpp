/***************************************************************************
   Copyright 2015 Ufora Inc.

   Licensed under the Apache License, Version 2.0 (the "License");
   you may not use this file except in compliance with the License.
   You may obtain a copy of the License at

       http://www.apache.org/licenses/LICENSE-2.0

   Unless required by applicable law or agreed to in writing, software
   distributed under the License is distributed on an "AS IS" BASIS,
   WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
   See the License for the specific language governing permissions and
   limitations under the License.
****************************************************************************/
#include "LocalSchedulerEvent.hppml"

#include <stdint.h>
#include <boost/python.hpp>
#include "../../FORA/python/FORAPythonUtil.hppml"
#include "../../FORA/python/FORAPythonUtilSerialization.hppml"
#include "../../native/Registrar.hpp"
#include "../../core/python/CPPMLWrapper.hpp"
#include "../../core/containers/ImmutableTreeVector.py.hpp"

using namespace Cumulus;
using namespace SystemwideComputationScheduler;

class SchedulerEventWrapper :
		public native::module::Exporter<SchedulerEventWrapper> {
public:
		std::string	     getModuleName(void)
			{
			return "Cumulus";
			}

		void exportPythonWrapper()
			{
			using namespace boost::python;
			
			object cls = 
				FORAPythonUtil::exposeValueLikeCppmlType<LocalSchedulerEvent>()
					.class_()
				;

			PythonWrapper<ImmutableTreeVector<LocalSchedulerEvent> >::exportPythonInterface("LocalSchedulerEvent")
				.def("__getstate__", &FORAPythonUtilSerialization::serializeEntireObjectGraph<ImmutableTreeVector<LocalSchedulerEvent> >)
				.def("__setstate__", &FORAPythonUtilSerialization::deserializeEntireObjectGraph<ImmutableTreeVector<LocalSchedulerEvent> >)
				.enable_pickling()
				;

			def("LocalSchedulerEvent", cls);
			}

};

//explicitly instantiating the registration element causes the linker to need
//this file
template<>
char native::module::Exporter<SchedulerEventWrapper>::mEnforceRegistration =
		native::module::ExportRegistrar<
			SchedulerEventWrapper>::registerWrapper();




