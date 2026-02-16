module fields_interface_mod
use fields_list_mod,     only : fields_list
contains

subroutine ifs_field_create_c()
call fields_list%get()
end subroutine ifs_field_create_c

end module fields_interface_mod
